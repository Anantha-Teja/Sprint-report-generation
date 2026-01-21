#!/usr/bin/env python3
"""
Jira API Client for Sprint Report Generation
Handles authentication and data fetching from Jira REST API
"""

import os
import sys
import requests
from typing import Dict, List, Optional
import logging
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from the script's directory
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class JiraClient:
    """Client for interacting with Jira REST API"""

    def __init__(self, server_url: str, email: str = None, api_token: str = None):
        self.server_url = server_url.rstrip('/')
        self.email = email or os.getenv('JIRA_EMAIL')
        self.api_token = api_token or os.getenv('JIRA_API_TOKEN')

        if not self.email or not self.api_token:
            logger.error("Jira credentials not found. Set JIRA_EMAIL and JIRA_API_TOKEN environment variables.")
            sys.exit(1)

        self.session = requests.Session()
        self.session.auth = (self.email, self.api_token)
        self.session.headers.update({
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        })

    def test_connection(self) -> bool:
        """Test the Jira API connection"""
        try:
            url = f"{self.server_url}/rest/api/3/myself"
            response = self.session.get(url)
            response.raise_for_status()
            user_info = response.json()
            logger.info(f"✅ Connected to Jira as: {user_info.get('displayName')}")
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Failed to connect to Jira: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response: {e.response.status_code}")
                logger.error(f"Body: {e.response.text}")
            return False

    def search_issues(self, jql: str, max_results: int = 2000) -> List[Dict]:
        """Search for issues using Jira JQL (new API endpoint)"""
        try:
            url = f"{self.server_url}/rest/api/3/search/jql"

            all_issues = []
            start_at = 0

            while True:
                # New API format - need to request fields explicitly
                params = {
                    "jql": jql,
                    "startAt": start_at,
                    "maxResults": min(max_results, 100),
                    "fields": "*all"  # Request all fields
                }

                response = self.session.get(url, params=params)
                response.raise_for_status()
                data = response.json()

                issues = data.get("issues", [])
                all_issues.extend(issues)

                logger.info(f"Fetched {len(issues)} issues (total: {len(all_issues)})")

                total = data.get("total", 0)
                if start_at + len(issues) >= total or len(issues) == 0:
                    break

                start_at += len(issues)

            logger.info(f"Total issues fetched: {len(all_issues)}")
            return all_issues

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed JQL search: {e}")
            if hasattr(e, "response") and e.response is not None:
                logger.error(f"Response status: {e.response.status_code}")
                logger.error(f"Response body: {e.response.text}")
            return []

    def get_sprint_issues(self, sprint_name: str, project_key: str) -> List[Dict]:
        """Fetch issues belonging to a specific sprint"""
        jql = f'project = {project_key} AND sprint = "{sprint_name}" ORDER BY issueType, created'
        logger.info(f"Running JQL: {jql}")
        return self.search_issues(jql)

    def parse_issue(self, issue: Dict) -> Dict:
        """Normalize Jira fields for consistent handling"""
        fields = issue.get("fields", {})

        # Epic Link - in new API, parent field is populated for epic relationship
        parent = fields.get("parent", {})
        epic_link = ""
        if parent and parent.get("fields", {}).get("issuetype", {}).get("name") == "Epic":
            epic_link = parent.get("key", "")
        
        # Fallback to customfield_10014 if parent is not an epic
        if not epic_link:
            epic_link = fields.get("customfield_10014", "")
        
        # Epic Name (only for issues that ARE epics)
        epic_name = fields.get("customfield_10011", "")

        # Sprint field
        sprint_field = fields.get("customfield_10020", [])
        sprint_name = ""
        if sprint_field and isinstance(sprint_field, list) and len(sprint_field) > 0:
            sprint_name = sprint_field[0].get("name", "")

        return {
            "key": issue.get("key", ""),
            "summary": fields.get("summary", ""),
            "description": fields.get("description", ""),
            "status": fields.get("status", {}).get("name", ""),
            "status_category": fields.get("status", {}).get("statusCategory", {}).get("name", ""),
            "issuetype": fields.get("issuetype", {}).get("name", ""),

            # Epic relationship
            "epic_link": epic_link,
            "epic_name": epic_name,

            # Parent info (for reference)
            "parent_key": parent.get("key", "") if parent else "",
            "parent_summary": parent.get("fields", {}).get("summary", "") if parent else "",

            "assignee": fields.get("assignee", {}).get("displayName", "") if fields.get("assignee") else "",
            "created": fields.get("created", ""),
            "updated": fields.get("updated", ""),
            "sprint": sprint_name
        }

    def group_issues_by_parent(self, issues: List[Dict]) -> Dict:
        """Group issues under their epics correctly"""
        epics = {}
        standalone = []

        for issue in issues:
            issue_type = issue.get("issuetype", "")

            # If the issue is an Epic, register it
            if issue_type == "Epic":
                epics[issue["key"]] = {
                    "epic_key": issue["key"],
                    "epic_name": issue["epic_name"] or issue["summary"],
                    "children": []
                }
                continue

            # If the issue belongs to an epic (Epic Link)
            epic_key = issue.get("epic_link")

            if epic_key:
                if epic_key not in epics:
                    epics[epic_key] = {
                        "epic_key": epic_key,
                        "epic_name": "",
                        "children": []
                    }
                epics[epic_key]["children"].append(issue)

            else:
                standalone.append(issue)

        logger.info(f"Grouped {len(epics)} epics and {len(standalone)} standalone issues")

        return {
            "epics": epics,
            "standalone": standalone
        }
