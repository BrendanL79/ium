#!/usr/bin/env python3
"""
Docker Image Auto-Update with Specific Tag Tracking

This script monitors Docker images for updates by comparing the 'latest' tag
with version-specific tags that match user-defined regex patterns.
"""

import json
import re
import subprocess
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import argparse
import os
import hashlib
import requests


class DockerImageUpdater:
    def __init__(self, config_file: str, state_file: str = "docker_update_state.json", dry_run: bool = False):
        """
        Initialize the Docker Image Updater.
        
        Args:
            config_file: Path to JSON configuration file
            state_file: Path to store state between runs
            dry_run: If True, only log what would be done without making changes
        """
        self.config_file = config_file
        self.state_file = state_file
        self.dry_run = dry_run
        self.config = self.load_config()
        self.state = self.load_state()
        
    def load_config(self) -> Dict:
        """Load configuration from JSON file."""
        try:
            with open(self.config_file, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            print(f"Config file {self.config_file} not found")
            sys.exit(1)
        except json.JSONDecodeError as e:
            print(f"Error parsing config file: {e}")
            sys.exit(1)
            
    def load_state(self) -> Dict:
        """Load previous state from file."""
        try:
            with open(self.state_file, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError:
            return {}
            
    def save_state(self):
        """Save current state to file."""
        with open(self.state_file, 'w') as f:
            json.dump(self.state, f, indent=2)
            
    def get_docker_token(self, image: str) -> Optional[str]:
        """
        Get authentication token for Docker registry.
        
        Args:
            image: Full image name (e.g., 'linuxserver/calibre')
            
        Returns:
            Authentication token or None
        """
        # Parse registry and image name
        if '/' in image and not image.startswith('docker.io/'):
            namespace, repo = image.split('/', 1)
        else:
            namespace = 'library'
            repo = image.split('/')[-1]
            
        # Docker Hub authentication endpoint
        auth_url = f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{namespace}/{repo}:pull"
        
        try:
            response = requests.get(auth_url)
            response.raise_for_status()
            return response.json().get('token')
        except Exception as e:
            print(f"Error getting Docker token for {image}: {e}")
            return None
            
    def get_manifest_digest(self, image: str, tag: str, token: str) -> Optional[str]:
        """
        Get manifest digest for a specific image:tag.
        
        Args:
            image: Image name
            tag: Tag name
            token: Authentication token
            
        Returns:
            Manifest digest or None
        """
        # Parse image name
        if '/' in image:
            namespace, repo = image.split('/', 1)
        else:
            namespace = 'library'
            repo = image
            
        # Docker Hub manifest endpoint
        manifest_url = f"https://registry-1.docker.io/v2/{namespace}/{repo}/manifests/{tag}"
        
        headers = {
            'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.docker.distribution.manifest.list.v2+json,application/vnd.docker.distribution.manifest.v2+json'
        }
        
        try:
            response = requests.get(manifest_url, headers=headers)
            response.raise_for_status()
            
            # Get digest from headers
            digest = response.headers.get('Docker-Content-Digest')
            return digest
        except Exception as e:
            print(f"Error getting manifest for {image}:{tag}: {e}")
            return None
            
    def get_all_tags(self, image: str, token: str) -> List[str]:
        """
        Get all available tags for an image.
        
        Args:
            image: Image name
            token: Authentication token
            
        Returns:
            List of available tags
        """
        # Parse image name
        if '/' in image:
            namespace, repo = image.split('/', 1)
        else:
            namespace = 'library'
            repo = image
            
        # Docker Hub tags endpoint
        tags_url = f"https://registry-1.docker.io/v2/{namespace}/{repo}/tags/list"
        
        headers = {
            'Authorization': f'Bearer {token}'
        }
        
        try:
            response = requests.get(tags_url, headers=headers)
            response.raise_for_status()
            return response.json().get('tags', [])
        except Exception as e:
            print(f"Error getting tags for {image}: {e}")
            return []
            
    def find_matching_tag(self, image: str, base_tag: str, regex_pattern: str) -> Optional[Tuple[str, str]]:
        """
        Find a tag matching the regex pattern that has the same digest as the base tag.
        
        Args:
            image: Image name
            base_tag: Base tag to track (e.g., 'latest', 'stable', '14')
            regex_pattern: Regex pattern to match tags
            
        Returns:
            Tuple of (matching_tag, digest) or None
        """
        # Get authentication token
        token = self.get_docker_token(image)
        if not token:
            return None
            
        # Get digest for base tag
        base_digest = self.get_manifest_digest(image, base_tag, token)
        if not base_digest:
            print(f"Could not get digest for {image}:{base_tag}")
            return None
            
        # Get all available tags
        all_tags = self.get_all_tags(image, token)
        if not all_tags:
            print(f"Could not get tags for {image}")
            return None
            
        # Compile regex pattern
        try:
            pattern = re.compile(regex_pattern)
        except re.error as e:
            print(f"Invalid regex pattern '{regex_pattern}': {e}")
            return None
            
        # Find tags matching the pattern
        matching_tags = [tag for tag in all_tags if pattern.match(tag)]
        
        # Find which matching tag has the same digest as base tag
        for tag in matching_tags:
            tag_digest = self.get_manifest_digest(image, tag, token)
            if tag_digest == base_digest:
                return (tag, base_digest)
                
        print(f"No tag matching pattern '{regex_pattern}' found with same digest as {base_tag}")
        return None
        
    def pull_image(self, image: str, tag: str) -> bool:
        """
        Pull a Docker image.
        
        Args:
            image: Image name
            tag: Tag to pull
            
        Returns:
            True if successful, False otherwise
        """
        full_image = f"{image}:{tag}"
        
        if self.dry_run:
            print(f"[DRY RUN] Would pull {full_image}")
            return True
        
        print(f"Pulling {full_image}...")
        
        try:
            result = subprocess.run(
                ['docker', 'pull', full_image],
                capture_output=True,
                text=True,
                check=True
            )
            print(f"Successfully pulled {full_image}")
            return True
        except subprocess.CalledProcessError as e:
            print(f"Error pulling {full_image}: {e.stderr}")
            return False
            
    def update_container(self, container_name: str, image: str, tag: str) -> bool:
        """
        Update a running container with a new image.
        
        Args:
            container_name: Name of the container to update
            image: Image name
            tag: Tag to use
            
        Returns:
            True if successful, False otherwise
        """
        full_image = f"{image}:{tag}"
        
        if self.dry_run:
            print(f"[DRY RUN] Would update container {container_name} with image {full_image}")
            print(f"[DRY RUN] Would stop container {container_name}")
            print(f"[DRY RUN] Would remove old container {container_name}")
            print(f"[DRY RUN] Would create new container {container_name} with image {full_image}")
            return True
        
        try:
            # Get current container info
            inspect_cmd = ['docker', 'inspect', container_name]
            result = subprocess.run(inspect_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"Container {container_name} not found")
                return False
                
            container_info = json.loads(result.stdout)[0]
            
            # Stop the container
            print(f"Stopping container {container_name}...")
            subprocess.run(['docker', 'stop', container_name], check=True)
            
            # Remove the old container
            print(f"Removing old container {container_name}...")
            subprocess.run(['docker', 'rm', container_name], check=True)
            
            # Create new container with same configuration
            # This is a simplified version - you may need to preserve more settings
            create_cmd = [
                'docker', 'run', '-d',
                '--name', container_name,
                '--restart', container_info['HostConfig']['RestartPolicy']['Name']
            ]
            
            # Add port mappings
            for port_mapping in container_info['HostConfig'].get('PortBindings', {}).values():
                if port_mapping:
                    for mapping in port_mapping:
                        host_port = mapping['HostPort']
                        container_port = list(container_info['HostConfig']['PortBindings'].keys())[0]
                        create_cmd.extend(['-p', f"{host_port}:{container_port}"])
            
            # Add volume mappings
            for mount in container_info['Mounts']:
                if mount['Type'] == 'bind':
                    create_cmd.extend(['-v', f"{mount['Source']}:{mount['Destination']}"])
                    
            # Add environment variables
            for env_var in container_info['Config'].get('Env', []):
                create_cmd.extend(['-e', env_var])
                
            # Add the image
            create_cmd.append(full_image)
            
            # Create and start new container
            print(f"Creating new container {container_name} with image {full_image}...")
            subprocess.run(create_cmd, check=True)
            
            print(f"Successfully updated container {container_name}")
            return True
            
        except Exception as e:
            print(f"Error updating container {container_name}: {e}")
            return False
            
    def check_and_update(self):
        """Check for updates and apply them if configured."""
        if self.dry_run:
            print("=== DRY RUN MODE ===")
            print("No actual changes will be made. Showing what would be done:\n")
            
        updates_found = []
        
        for image_config in self.config.get('images', []):
            image = image_config['image']
            regex = image_config['regex']
            base_tag = image_config.get('base_tag', 'latest')  # Default to 'latest' if not specified
            auto_update = image_config.get('auto_update', False)
            container_name = image_config.get('container_name')
            
            print(f"\nChecking {image}:{base_tag}...")
            
            # Find matching tag for current base tag
            result = self.find_matching_tag(image, base_tag, regex)
            
            if result:
                matching_tag, digest = result
                print(f"Base tag '{base_tag}' corresponds to: {matching_tag}")
                print(f"Digest: {digest}")
                
                # Check if this is different from our saved state
                saved_info = self.state.get(image, {})
                saved_tag = saved_info.get('tag')
                saved_digest = saved_info.get('digest')
                
                if saved_digest != digest:
                    print(f"UPDATE AVAILABLE: {saved_tag or 'unknown'} -> {matching_tag}")
                    updates_found.append({
                        'image': image,
                        'base_tag': base_tag,
                        'old_tag': saved_tag,
                        'new_tag': matching_tag,
                        'digest': digest
                    })
                    
                    if auto_update:
                        # Pull the new image with base tag
                        if self.pull_image(image, base_tag):
                            # Also pull the specific tag
                            self.pull_image(image, matching_tag)
                            
                            # Update container if specified
                            if container_name:
                                # Use base_tag for container update
                                self.update_container(container_name, image, base_tag)
                            
                            # Update state (only if not dry run)
                            if not self.dry_run:
                                self.state[image] = {
                                    'base_tag': base_tag,
                                    'tag': matching_tag,
                                    'digest': digest,
                                    'last_updated': datetime.now().isoformat()
                                }
                            else:
                                print(f"[DRY RUN] Would update state for {image}")
                                print(f"[DRY RUN] Would save: base_tag={base_tag}, tag={matching_tag}, digest={digest}")
                else:
                    print("No update available")
                    
        # Save state (only if not dry run)
        if not self.dry_run:
            self.save_state()
        else:
            print("[DRY RUN] Would save state to file")
        
        # Summary
        if updates_found:
            print("\n=== Update Summary ===")
            for update in updates_found:
                print(f"{update['image']}: {update['old_tag'] or 'unknown'} -> {update['new_tag']}")
        else:
            print("\nNo updates found")
            
        return updates_found


def main():
    parser = argparse.ArgumentParser(description='Docker image auto-updater with tag tracking')
    parser.add_argument('config', help='Path to configuration JSON file')
    parser.add_argument('--state', default='docker_update_state.json', 
                       help='Path to state file (default: docker_update_state.json)')
    parser.add_argument('--check-only', action='store_true',
                       help='Only check for updates, don\'t apply them')
    parser.add_argument('--daemon', action='store_true',
                       help='Run continuously, checking at intervals')
    parser.add_argument('--interval', type=int, default=3600,
                       help='Check interval in seconds when running as daemon (default: 3600)')
    parser.add_argument('--dry-run', action='store_true',
                       help='Show what would be done without making any changes')
    
    args = parser.parse_args()
    
    # Load updater
    updater = DockerImageUpdater(args.config, args.state, args.dry_run)
    
    if args.daemon:
        print(f"Running in daemon mode, checking every {args.interval} seconds")
        while True:
            try:
                updater.check_and_update()
                print(f"\nSleeping for {args.interval} seconds...")
                time.sleep(args.interval)
            except KeyboardInterrupt:
                print("\nExiting...")
                break
    else:
        updater.check_and_update()


if __name__ == '__main__':
    main()

