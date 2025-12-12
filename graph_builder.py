import os
import re
import pickle
import hashlib
from logger import log

CACHE_FILE = "kg_cache.pkl"

class GoKnowledgeGraph:
    def __init__(self, root_dir):
        self.root_dir = root_dir
        # Structure: { 'func_name': {'file': path, 'line': int, 'type': 'func'|'struct', ...} }
        self.definitions = {}
        # Structure: { 'file_absolute_path': 'md5_hash_string' }
        self.file_hashes = {}

    def _calculate_file_hash(self, filepath):
        """Calculates MD5 hash of the file content to detect changes."""
        hasher = hashlib.md5()
        try:
            with open(filepath, 'rb') as f:
                buf = f.read()
                hasher.update(buf)
            return hasher.hexdigest()
        except Exception:
            return None

    def load_cache(self):
        """Attempts to load the knowledge graph from a local pickle file."""
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, 'rb') as f:
                    data = pickle.load(f)
                    self.definitions = data.get('definitions', {})
                    self.file_hashes = data.get('hashes', {})
                log.info(f"Box Loaded Knowledge Graph from cache ({len(self.definitions)} nodes).")
                return True
            except Exception as e:
                log.warning(f"Failed to load cache: {e}")
        return False

    def save_cache(self):
        """Saves the current knowledge graph and file hashes to disk."""
        try:
            with open(CACHE_FILE, 'wb') as f:
                pickle.dump({
                    'definitions': self.definitions,
                    'hashes': self.file_hashes
                }, f)
            log.info("Saved Knowledge Graph to disk cache.")
        except Exception as e:
            log.error(f"Failed to save cache: {e}")

    def parse_project(self, changed_files=None):
        """
        Parses the Go project.
        
        Args:
            changed_files (list, optional): List of absolute file paths that have changed.
                                            If provided, performs an incremental update.
        """
        # 1. Try to load existing cache
        cache_loaded = self.load_cache()

        log.info(f"Building/Updating Knowledge Graph from: {self.root_dir}")
        
        # 2. If cache is missing or cold start, force full scan
        if not cache_loaded:
            log.info("Cache miss or cold start. Performing full scan...")
            self._scan_directory(self.root_dir)
        else:
            # 3. Incremental update strategy
            if changed_files:
                log.info(f"Incremental update mode. processing {len(changed_files)} changed files.")
                for file_path in changed_files:
                    if file_path.endswith('.go') and os.path.exists(file_path):
                        self._process_file_if_changed(file_path)
            else:
                # Fallback: If no git info provided, scan directory and check hashes
                log.info("Checking file hashes for changes...")
                self._scan_directory_incremental(self.root_dir)
        
        # 4. Save the updated state
        self.save_cache()
        log.info(f"Knowledge Graph ready. Total nodes: {len(self.definitions)}")

    def _scan_directory(self, directory):
        """Recursively scans directory and parses all .go files (Full Scan)."""
        for root, _, files in os.walk(directory):
            for file in files:
                if file.endswith(".go"):
                    file_path = os.path.join(root, file)
                    self._parse_file(file_path)

    def _scan_directory_incremental(self, directory):
        """Scans directory but only parses files if their hash has changed."""
        for root, _, files in os.walk(directory):
            for file in files:
                if file.endswith(".go"):
                    file_path = os.path.join(root, file)
                    self._process_file_if_changed(file_path)

    def _process_file_if_changed(self, file_path):
        """Checks hash and reparses file only if changed."""
        current_hash = self._calculate_file_hash(file_path)
        
        # Check if file is new or modified
        if file_path not in self.file_hashes or self.file_hashes[file_path] != current_hash:
            # log.debug(f"File changed, reparsing: {os.path.basename(file_path)}")
            self._parse_file(file_path)

    def _parse_file(self, file_path):
        """Parses a single Go file using Regex to find functions and structs."""
        # Update hash
        self.file_hashes[file_path] = self._calculate_file_hash(file_path)
        
        # Clean up old entries for this file to avoid duplicates/stale data
        self._remove_file_entries(file_path)
        
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()

            for i, line in enumerate(lines):
                line = line.strip()
                
                # Regex for functions/methods: func (r Receiver) Name(...)
                # or func Name(...)
                func_match = re.search(r'^func\s+(?:\(\s*[\w*]+\s+[\w*]+\s*\)\s+)?(\w+)\s*\(', line)
                if func_match:
                    func_name = func_match.group(1)
                    self.definitions[func_name] = {
                        'type': 'func',
                        'file': file_path,
                        'line': i + 1,
                        'desc': self._extract_doc_comment(lines, i)
                    }
                    continue

                # Regex for structs: type Name struct
                struct_match = re.search(r'^type\s+(\w+)\s+struct', line)
                if struct_match:
                    struct_name = struct_match.group(1)
                    self.definitions[struct_name] = {
                        'type': 'struct',
                        'file': file_path,
                        'line': i + 1,
                        'desc': self._extract_doc_comment(lines, i)
                    }

        except Exception as e:
            log.error(f"Error parsing file {file_path}: {e}")

    def _remove_file_entries(self, file_path):
        """Removes all graph nodes belonging to a specific file."""
        keys_to_remove = [k for k, v in self.definitions.items() if v['file'] == file_path]
        for k in keys_to_remove:
            del self.definitions[k]

    def _extract_doc_comment(self, lines, current_line_idx):
        """Extracts comments immediately preceding the definition."""
        comments = []
        idx = current_line_idx - 1
        while idx >= 0:
            line = lines[idx].strip()
            if line.startswith('//'):
                comments.insert(0, line.lstrip('/ ').strip())
                idx -= 1
            else:
                break
        return " ".join(comments)

    def format_graph_report(self, func_name):
        """Generates a text report for a specific function/node."""
        if func_name not in self.definitions:
            return None
        
        info = self.definitions[func_name]
        return (
            f"- **{info['type']} {func_name}**\n"
            f"  - Location: `{os.path.basename(info['file'])}:{info['line']}`\n"
            f"  - Doc: {info['desc'] if info['desc'] else 'No documentation.'}"
        )