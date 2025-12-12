import subprocess
import os
from typing import List, Optional

class GitHelper:
    def __init__(self, repo_path: str):
        self.repo_path = repo_path
        if not os.path.exists(os.path.join(repo_path, ".git")):
            raise ValueError(f"Invalid git repository path: {repo_path}")

    def _run_git_cmd(self, args: List[str]) -> str:
        try:
            cmd = ["git", "-C", self.repo_path] + args
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                encoding='utf-8'
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            print(f"Error running git command: {' '.join(cmd)}")
            print(f"Stderr: {e.stderr}")
            raise e

    def get_default_branch(self) -> str:
        try:
            branches = self._run_git_cmd(["branch", "-r"]).split('\n')
            for branch in branches:
                if "origin/main" in branch:
                    return "origin/main"
                if "origin/master" in branch:
                    return "origin/master"
            return "main" # Fallback
        except:
            return "main"

    def get_changed_files(self, target_branch: str = None) -> List[str]:
        if not target_branch:
            target_branch = self.get_default_branch()

        output = self._run_git_cmd(["diff", "--name-only", target_branch])
        if not output:
            return []
        
        return output.split('\n')

    def get_project_diff(self, target_branch: str = None) -> str:
        if not target_branch:
            target_branch = self.get_default_branch()

        exclude_patterns = [
            ":!go.sum",           
            ":!go.mod",           
            ":!*.lock",          
            ":!*.svg",           
            ":!*.png",
            ":!assets/*",        
            ":!vendor/*"
        ]

        args = ["diff", target_branch, "--", "."] + exclude_patterns
        
        return self._run_git_cmd(args)

    def get_pr_description_context(self) -> str:
        return self._run_git_cmd(["log", "-1", "--pretty=format:Commit: %h%nAuthor: %an%nDate: %cd%n%nMessage:%n%s%n%b"])