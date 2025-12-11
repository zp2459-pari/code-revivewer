import os
import re
import json
from logger import log 

class GoKnowledgeGraph:
    def __init__(self, root_dir):
        self.root_dir = root_dir
        # {"func_name": {"file": "path", "line": 10, "code": "..."}}
        self.definitions = {} 
        # {"func_name": ["called_func_1", "called_func_2"]}
        self.references = {}
        # {"StructName": "file:line"}
        self.structs = {}

    def parse_project(self):
        log.info(f"🕸️ Building Knowledge Graph from: {self.root_dir}")
        
        for root, _, files in os.walk(self.root_dir):
            for file in files:
                if file.endswith(".go") and not file.endswith("_test.go"):
                    path = os.path.join(root, file)
                    self._scan_definitions(path)
        
        log.info(f"✅ Found {len(self.definitions)} functions/methods and {len(self.structs)} structs.")

        self._scan_references()

    def _scan_definitions(self, file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            lines = content.splitlines()

        # func (c *Checker) RunChecks(...) 
        # or : func RunChecks(...)
        func_pattern = re.compile(r'^func\s+(?:\((?:[^)]+)\)\s+)?([A-Za-z0-9_]+)\s*\(')
        
        # match: type Checker struct {
        struct_pattern = re.compile(r'^type\s+([A-Za-z0-9_]+)\s+struct')

        for i, line in enumerate(lines):
            line = line.strip()
            
            match_func = func_pattern.search(line)
            if match_func:
                func_name = match_func.group(1)
                self.definitions[func_name] = {
                    "file": file_path,
                    "line": i + 1,
                    "signature": line 
                }
                continue

            match_struct = struct_pattern.search(line)
            if match_struct:
                struct_name = match_struct.group(1)
                self.structs[struct_name] = {
                    "file": file_path,
                    "line": i + 1
                }

    def _scan_references(self):

        for func_name, info in self.definitions.items():
            self.references[func_name] = []
            
            try:
                with open(info['file'], "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                continue


            for other_func in self.definitions:
                if func_name == other_func:
                    continue
                
                if other_func in content: 
                    if re.search(r'\b' + re.escape(other_func) + r'\b', content):
                        self.references[func_name].append(other_func)

    def get_related_context(self, changed_func_name):

        context = {
            "target": changed_func_name,
            "called_by": [], 
            "calls_to": []   
        }

        if changed_func_name in self.references:
            context["calls_to"] = self.references[changed_func_name]

        for f, calls in self.references.items():
            if changed_func_name in calls:
                context["called_by"].append(f)

        return context

    def format_graph_report(self, changed_func_name):
        data = self.get_related_context(changed_func_name)
        
        if not data["called_by"] and not data["calls_to"]:
            return ""

        report = f"### 🕸️ Code Knowledge Graph (Impact Analysis)\n"
        report += f"**Function `{changed_func_name}` Analysis:**\n"
        
        if data["called_by"]:
            report += f"- ⬆️ **Called By (Impacted Upstream):** {', '.join(data['called_by'])}\n"
        
        if data["calls_to"]:
            report += f"- ⬇️ **Calls To (Dependencies):** {', '.join(data['calls_to'])}\n"
            
        report += "\n(Hint: If you modify logic, check if these upstream callers need adjustment.)\n"
        return report
