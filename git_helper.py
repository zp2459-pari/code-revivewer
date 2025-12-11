import subprocess
# TODO
def get_pr_diff(target_branch="main"):
    cmd = ["git", "diff", target_branch, "--", "*.go"]
    diff_output = subprocess.check_output(cmd, text=True)
    return diff_output

def get_related_files(diff_output):

    pass