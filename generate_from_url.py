#!/usr/bin/env python3
"""
Generate Sprint Report from Jira Issue Navigator URL
Extracts JQL from the URL and generates the report
"""

import argparse
import sys
from urllib.parse import urlparse, parse_qs, unquote
from generate_api_report import APIReportGenerator
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def extract_jql_from_url(url: str) -> str:
    """
    Extract JQL query from Jira issue navigator URL
    
    Args:
        url: Full Jira issue navigator URL
        
    Returns:
        JQL query string
    """
    try:
        # Parse the URL
        parsed = urlparse(url)
        
        # Get query parameters
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
            logger.info(f"Extracted JQL: {jql}")
            return jql
        else:
            logger.error("No JQL found in URL. Make sure you're using the issue navigator URL.")
            logger.info("URL should look like: https://hpe.atlassian.net/issues/?jql=...")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"Error parsing URL: {e}")
        sys.exit(1)


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Generate sprint report from Jira Issue Navigator URL',
        epilog='''
Examples:
  # From issue navigator URL
  python3 generate_from_url.py --url "https://hpe.atlassian.net/issues/?jql=project%20%3D%20GLCP..."
  
  # With custom output
  python3 generate_from_url.py --url "..." --output my_report.md
        '''
    )
    parser.add_argument(
        '--url',
        required=False,
        help='Jira issue navigator URL (copy from browser address bar)'
    )
    parser.add_argument(
        '--config',
        default='config.yaml',
        help='Path to YAML configuration file (default: config.yaml)'
    )
    parser.add_argument(
        '--output',
        required=False,
        help='Output markdown file path (default: sprint_report.md or config)'
    )

    args = parser.parse_args()

    # Load config to get defaults if needed
    import yaml
    with open(args.config, 'r') as f:
        config = yaml.safe_load(f)

    # Determine URL: CLI arg > config > error
    url = args.url or config.get('jira_url')
    if not url:
        logger.error("No Jira URL provided. Use --url or set jira_url in config.yaml.")
        sys.exit(1)

    # Determine output file: CLI arg > config > default
    output_file = args.output or config.get('output_file') or 'sprint_report.md'

    # Extract JQL from URL
    print("=" * 80)
    print("EXTRACTING JQL FROM ISSUE NAVIGATOR URL")
    print("=" * 80)
    jql = extract_jql_from_url(url)
    print(f"\n✅ JQL Query: {jql}\n")

    # Generate report
    generator = APIReportGenerator(config_path=args.config)

    # Override the JQL query from config with the one from URL
    generator.config['jira']['jql_query'] = jql

    # Test connection first
    if not generator.client.test_connection():
        logger.error("Failed to connect to Jira. Please check your credentials.")
        sys.exit(1)

    report = generator.generate_markdown_report(output_path=output_file)

    print("\n" + "=" * 80)
    print("✅ REPORT GENERATED SUCCESSFULLY")
    print("=" * 80)
    print(f"Output: {output_file}")
    print("\nNext steps:")
    print("1. Review the markdown file")
    print("2. Convert to HTML if needed")
    print()


if __name__ == "__main__":
    main()
