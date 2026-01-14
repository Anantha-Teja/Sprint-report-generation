#!/usr/bin/env python3
"""
Sprint Report Generator - Refined Version
Generates sprint reports from Jira Issue Navigator URL and config.yaml
"""

import yaml
import argparse
import sys
import re
from typing import Dict, List
from urllib.parse import urlparse, parse_qs, unquote
from jira_client import JiraClient
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class SprintReportGenerator:
    """Generate sprint reports from Jira Issue Navigator URL"""
    
    def __init__(self, config_path: str = 'config.yaml'):
        """Initialize generator with config file"""
        self.config_path = config_path
        self.config = self._load_config()
        self._validate_config()
        
        # Initialize Jira client
        jira_config = self.config.get('jira', {})
        self.client = JiraClient(server_url=jira_config.get('server_url'))
    
    def _load_config(self) -> Dict:
        """Load YAML configuration file"""
        try:
            with open(self.config_path, 'r') as f:
                config = yaml.safe_load(f)
            logger.info(f"✅ Loaded configuration from {self.config_path}")
            return config
        except FileNotFoundError:
            logger.error(f"❌ Config file not found: {self.config_path}")
            sys.exit(1)
        except Exception as e:
            logger.error(f"❌ Error loading configuration: {e}")
            sys.exit(1)
    
    def _validate_config(self):
        """Validate that all required config sections exist"""
        required_sections = ['sprint_info', 'jira', 'output']
        for section in required_sections:
            if section not in self.config:
                logger.error(f"❌ Missing required section in config: {section}")
                sys.exit(1)
        
        # Validate sprint_info
        sprint_info = self.config.get('sprint_info', {})
        required_sprint_fields = ['sprint_number', 'start_date', 'end_date', 'team_name']
        for field in required_sprint_fields:
            if field not in sprint_info:
                logger.error(f"❌ Missing required field in sprint_info: {field}")
                sys.exit(1)
        
        # Validate jira config
        jira_config = self.config.get('jira', {})
        if 'server_url' not in jira_config:
            logger.error(f"❌ Missing server_url in jira config")
            sys.exit(1)
        
        if 'issue_navigator_url' not in jira_config or not jira_config['issue_navigator_url']:
            logger.error(f"❌ Missing issue_navigator_url in jira config")
            sys.exit(1)
        
        logger.info("✅ Configuration validated successfully")
    
    def _extract_jql_from_url(self, url: str) -> str:
        """Extract JQL query from Jira issue navigator URL"""
        try:
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            
            # Look for JQL in different possible parameter names
            jql = None
            if 'jql' in params:
                jql = params['jql'][0]
            elif 'jqlQuery' in params:
                jql = params['jqlQuery'][0]
            
            if jql:
                # Decode URL encoding
                jql = unquote(jql)
                logger.info(f"📋 Extracted JQL from URL")
                return jql
            else:
                logger.error("❌ No JQL found in URL")
                logger.info("URL should contain: ?jql=...")
                sys.exit(1)
                
        except Exception as e:
            logger.error(f"❌ Error parsing URL: {e}")
            sys.exit(1)
    
    def fetch_sprint_data(self) -> Dict:
        """Fetch sprint issues from Jira using the issue navigator URL"""
        jira_config = self.config.get('jira', {})
        issue_navigator_url = jira_config.get('issue_navigator_url')
        
        # Extract JQL from URL
        jql = self._extract_jql_from_url(issue_navigator_url)
        
        logger.info(f"🔍 Fetching issues from Jira...")
        raw_issues = self.client.search_issues(jql, max_results=500)

        # Parse issues
        parsed_issues = [self.client.parse_issue(issue) for issue in raw_issues]
        
        valid_issue_keys = set(issue['key'] for issue in parsed_issues)
        logger.info(f"✅ Found {len(valid_issue_keys)} issues from JQL")

        # FIX #1: Use epic_link instead of parent_key
        # Collect epic keys that are either:
        # 1. Present in the original JQL results (epics themselves)
        # 2. Referenced by epic_link in children
        epics_in_jql = set()
        epic_links_from_children = set()
        
        for issue in parsed_issues:
            issue_type = issue.get('issuetype', '')
            
            # If this issue IS an epic, include it
            if issue_type == 'Epic':
                epics_in_jql.add(issue['key'])
            
            # If this issue has an epic link, track it
            epic_link = issue.get('epic_link')
            if epic_link:
                epic_links_from_children.add(epic_link)

        # FIX #2 & #3: Only fetch epic details for epics that are REFERENCED by children
        # We don't fetch epics that aren't in the JQL results unless they have children in the sprint
        epic_keys_to_fetch = epic_links_from_children - epics_in_jql
        
        logger.info(f"📊 Epics in JQL: {len(epics_in_jql)}, Epic links from children: {len(epic_links_from_children)}")
        
        epic_details = {}
        
        # Add epic details for epics that were in the original JQL
        for issue in parsed_issues:
            if issue.get('issuetype') == 'Epic':
                epic_details[issue['key']] = issue
        
        # Fetch details for epic links that weren't in the original JQL
        if epic_keys_to_fetch:
            logger.info(f"🔍 Fetching {len(epic_keys_to_fetch)} additional epic details...")
            epic_jql = f"key in ({','.join(epic_keys_to_fetch)})"
            epic_raws = self.client.search_issues(epic_jql, max_results=len(epic_keys_to_fetch))
            for epic in epic_raws:
                epic_data = self.client.parse_issue(epic)
                epic_details[epic_data['key']] = epic_data
            logger.info(f"✅ Fetched details for {len(epic_keys_to_fetch)} additional epics")

        # Group by epic (using epic_link)
        grouped_data = self.client.group_issues_by_parent(parsed_issues)
        
        # FIX #3: Only keep epics that have children from the original JQL
        # or were themselves in the original JQL
        filtered_epics = {}
        for epic_key, epic_data in grouped_data['epics'].items():
            children = epic_data.get('children', [])
            
            # Keep epic if:
            # 1. It was in the original JQL, OR
            # 2. It has at least one child from the original JQL
            if epic_key in epics_in_jql or any(child['key'] in valid_issue_keys for child in children):
                # Filter children to only include those from original JQL
                filtered_children = [child for child in children if child['key'] in valid_issue_keys]
                epic_data['children'] = filtered_children
                
                if filtered_children or epic_key in epics_in_jql:
                    filtered_epics[epic_key] = epic_data
                    logger.info(f"  ✓ Including epic {epic_key} with {len(filtered_children)} children")
        
        grouped_data['epics'] = filtered_epics
        
        # Also filter standalone to only include issues from original JQL
        grouped_data['standalone'] = [issue for issue in grouped_data['standalone'] if issue['key'] in valid_issue_keys]
        
        # Attach epic details
        grouped_data['epic_details'] = epic_details
        
        logger.info(f"📊 Final count: {len(filtered_epics)} epics, {len(grouped_data['standalone'])} standalone issues")
        return grouped_data
    
    def determine_epic_status(self, epic_data: Dict) -> str:
        """Determine epic status based on children statuses"""
        children = epic_data.get('children', [])
        if not children:
            return 'Unknown'
        
        done_count = sum(1 for child in children if child.get('status_category', '').lower() == 'done')
        
        if done_count == len(children):
            return 'Done'
        else:
            return 'In Progress'
    
    def generate_epic_summary(self, epic_data: Dict) -> str:
        """Generate a smart summary of work done under the epic"""
        children = epic_data.get('children', [])
        total = len(children)
        epic_name = epic_data.get('epic_name', '').lower()
        
        done_children = [c for c in children if c.get('status_category', '').lower() == 'done']
        done_count = len(done_children)
        
        # Check if this is an Istio rollout epic
        if 'istio' in epic_name and 'rollout' in epic_name:
            version_match = re.search(r'(\d+\.\d+(?:\.\d+)?)\s*-\s*(\d+\.\d+(?:\.\d+)?)', epic_name)
            version_str = f"{version_match.group(1)} to {version_match.group(2)}" if version_match else "upgrade"
            
            service_env_counts = {}
            for child in done_children:
                summary = child.get('summary', '')
                match = re.search(r'\(([^)]+)\)', summary)
                if match:
                    service_env = match.group(1).strip()
                    parts = service_env.rsplit(' ', 1)
                    if len(parts) == 2:
                        service, env = parts
                        key = f"{service} {env}"
                        service_env_counts[key] = service_env_counts.get(key, 0) + 1
                    else:
                        service_env_counts[service_env] = service_env_counts.get(service_env, 0) + 1
            
            summary_parts = []
            for service_env, count in sorted(service_env_counts.items()):
                cluster_word = "cluster" if count == 1 else "clusters"
                summary_parts.append(f"{service_env} - {count} {cluster_word}")
            
            if summary_parts:
                details = "<br>".join(summary_parts)
                summary = f"Completed Istio {version_str} rollout:<br>{details}"
            else:
                summary = f"Completed Istio {version_str} rollout across {done_count} clusters."
        
        # Check if this is an ArgoCD migration epic
        elif 'argocd' in epic_name:
            component = epic_name.split('-')[-1].strip() if '-' in epic_name else "component"
            component = component.title()
            
            env_list = []
            for child in done_children:
                summary_text = child.get('summary', '')
                env_match = re.search(r'\[([^\]]+)\]', summary_text)
                if env_match:
                    env_list.append(env_match.group(1))
            
            if env_list:
                unique_envs = sorted(set(env_list))
                if len(unique_envs) == 1:
                    summary = f"Migrated {component} to ArgoCD in {unique_envs[0]}."
                else:
                    summary = f"Migrated {component} to ArgoCD across {', '.join(unique_envs)} environments."
            else:
                summary = f"Completed ArgoCD migration for {component}."
        
        # Generic summary for other epics
        else:
            if done_count == total and total > 0:
                summary = f"Completed all {total} related tasks."
            elif done_count > 0:
                summary = f"Completed {done_count} of {total} tasks."
            else:
                summary = f"{total} tasks tracked."
        
        return summary
    
    def generate_markdown_report(self, output_path: str = None) -> str:
        """Generate markdown sprint report"""
        logger.info("📝 Generating sprint report...")

        grouped_data = self.fetch_sprint_data()
        epics_dict = grouped_data['epics']
        standalone = grouped_data['standalone']
        epic_details = grouped_data.get('epic_details', {})

        sprint_info = self.config.get('sprint_info', {})
        metrics = self.config.get('metrics', {})

        epic_entries = []

        # Process epics
        for epic_key, epic_data in epics_dict.items():
            epic_info = epic_details.get(epic_key, {})
            epic_status = epic_info.get('status') or epic_data.get('status') or self.determine_epic_status(epic_data)
            targeted_sprint = epic_info.get('sprint', '')
            
            # Get epic name from epic_info (fetched epic details) first, then fallback to epic_data
            epic_name = epic_info.get('summary') or epic_info.get('epic_name') or epic_data.get('epic_name') or f"Epic {epic_key}"
            
            summary = self.generate_epic_summary(epic_data)

            epic_entries.append({
                'epic_key': epic_key,
                'description': epic_name,
                'targeted_sprint': targeted_sprint,
                'status': epic_status,
                'details': summary
            })

        # Add standalone issues
        for issue in standalone:
            status = issue.get('status') or issue.get('status_category', '')
            epic_entries.append({
                'epic_key': issue['key'],
                'description': issue['summary'],
                'targeted_sprint': '',
                'status': status,
                'details': issue['summary'][:200]
            })

        # Build markdown report
        md_lines = [
            f"# CCP K8s IST Sprint {sprint_info.get('sprint_number', '')} Report",
            "",
            f"This sprint report highlights the achievements and overall progress of the {sprint_info.get('team_name', '')} team during CCP Sprint {sprint_info.get('sprint_number', '')} (from {sprint_info.get('start_date', '')} to {sprint_info.get('end_date', '')}). Below are the key updates and appreciations.",
            "",
            "## A] Kudos/Appreciation",
            ""
        ]

        for kudo in self.config.get('kudos', []):
            md_lines.append(f"- {kudo}")

        # Sort epic_entries: Done first, then In Progress, then others
        def get_sort_order(entry):
            status = entry['status'].lower()
            if status == 'done':
                return 0
            elif status == 'in progress':
                return 1
            else:
                return 2
        
        epic_entries_sorted = sorted(epic_entries, key=get_sort_order)

        md_lines.extend([
            "",
            "## B] Epic Updates",
            "",
            "| Epic | Description | Targeted Sprint | Status | Details |",
            "|------|-------------|----------------|--------|---------|"
        ])

        for epic in epic_entries_sorted:
            epic_link = f"[{epic['epic_key']}](https://hpe.atlassian.net/browse/{epic['epic_key']})"
            md_lines.append(
                f"| {epic_link} | {epic['description']} | {epic['targeted_sprint']} | {epic['status']} | {epic['details']} |"
            )

        md_lines.extend([
            "",
            "## C] Overall Story Points Completed",
            "",
            f"**Story Points:** {metrics.get('story_points_completed', 0)}",
            "",
            "## D] Overall PR Reviews",
            "",
            f"**PRs Reviewed:** {metrics.get('pr_reviews_count', 0)}",
            "",
            "---",
            "",
            "Overall, the team demonstrated very good collaboration, dedication, and technical expertise, ensuring the successful delivery of all sprint commitments and setting a strong foundation for future work.",
            "",
            "Regards,"
        ])

        report_content = "\n".join(md_lines)

        # Save to file
        if output_path:
            with open(output_path, 'w') as f:
                f.write(report_content)
            logger.info(f"✅ Report saved to {output_path}")

        return report_content


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Generate sprint report from Jira Issue Navigator URL',
        epilog='Example: python3 generate_api_report.py --config config.yaml'
    )
    parser.add_argument(
        '--config',
        default='config.yaml',
        help='Path to YAML configuration file (default: config.yaml)'
    )
    parser.add_argument(
        '--output',
        default=None,
        help='Override output file path from config (optional)'
    )
    
    args = parser.parse_args()
    
    print("\n" + "=" * 80)
    print("SPRINT REPORT GENERATOR")
    print("=" * 80)
    print()
    
    # Generate report
    generator = SprintReportGenerator(config_path=args.config)
    
    # Test connection first
    logger.info("🔗 Testing Jira connection...")
    if not generator.client.test_connection():
        logger.error("❌ Failed to connect to Jira. Please check your credentials.")
        sys.exit(1)
    logger.info("✅ Jira connection successful")
    
    # Determine output file
    output_file = args.output or generator.config.get('output', {}).get('report_file', 'sprint_report.md')
    
    # Generate report
    report = generator.generate_markdown_report(output_path=output_file)
    
    print()
    print("=" * 80)
    print("✅ REPORT GENERATED SUCCESSFULLY")
    print("=" * 80)
    print(f"📄 Output File: {output_file}")
    print(f"🏃 Sprint: {generator.config.get('sprint_info', {}).get('sprint_name', 'N/A')}")
    print(f"👥 Team: {generator.config.get('sprint_info', {}).get('team_name', 'N/A')}")
    print(f"📅 Period: {generator.config.get('sprint_info', {}).get('start_date', '')} to {generator.config.get('sprint_info', {}).get('end_date', '')}")
    print()
    print("📋 For next sprint:")
    print("   1. Update config.yaml with new sprint info and Jira URL")
    print("   2. Run: python3 generate_api_report.py")
    print("=" * 80)
    print()


if __name__ == "__main__":
    main()
