"""
这个类需要处理文件的差异和变更检测，它可能会用到 FileHandler 类来访问文件系统。
ChangeDetector 类的核心在于能够识别自上次提交以来文件的变更。
"""
import git
import re
from .file_handler import FileHandler
import subprocess

EMPTY = '4b825dc642cb6eb9a060e54bf8d69288fbee4904'


class ChangeDetector:
    def __init__(self, repo_path):
        """
        Initializes a ChangeDetector object.

        Parameters:
        repo_path (str): The path to the repository.

        Returns:
        None
        """
        self.repo = git.Repo(repo_path)

    def get_staged_pys(self, since="HEAD"):
        """
        获取仓库中已经暂存的python文件变更。

        这个函数只追踪 Git 中已经暂存的 Python 文件变更，
        即那些已经执行了 `git add` 的文件。

        Returns:
            dict: 变更的python文件字典，键是文件路径，值是一个布尔值，表示这个文件是否是新建的
        """
        repo = self.repo
        staged_files = {}

        # 检测已暂存的变更
        # 请注意！GitPython库的逻辑和git不同。这里的R=True参数的作用是反转版本对比逻辑。
        # 在GitPython库中，repo.index.diff('HEAD')是将暂存区（index）作为新状态（new state）与原本HEAD的提交（old state）进行比较，这意味着，如果现在的暂存区中有一个新文件，它会显示为在HEAD中不存在，即被“删除”。
        # R=True就是把这个逻辑反转过来，把上一次的提交（HEAD）正确地作为旧状态与现在的暂存区（新状态）（Index）进行比较。在这种情况下，暂存区中的新文件会正确地显示为新增，因为它在HEAD中不存在。
        diffs = repo.index.diff(since, R=True)

        for diff in diffs:
            if diff.change_type in ["A", "M"] and diff.a_path.endswith(".py"):
                is_new_file = diff.change_type == "A"
                staged_files[diff.a_path] = is_new_file

        return staged_files


    def get_file_diff(self, file_path, is_new_file):
        """
        函数的作用是获取某个文件的变更内容。对于新文件，使用 git diff --staged 获取差异。
        Args:
            file_path (str): 文件相对路径
            is_new_file (bool): 指示文件是否是新文件
        Returns:
            list: 变更内容列表
        """
        repo = self.repo

        if is_new_file:
            # 对于新文件，先将其添加到暂存区
            add_command = f'git -C {repo.working_dir} add "{file_path}"'
            subprocess.run(add_command, shell=True, check=True)

            # 获取暂存区的diff
            diffs = repo.git.diff("--staged", EMPTY, file_path).splitlines()
        else:
            # 对于非新文件，获取HEAD的diff
            diffs = repo.git.diff("HEAD", file_path).splitlines()

        return diffs

    def parse_diffs(self, diffs):
        """
        解析差异内容，提取出添加和删除的对象信息，对象可以是类或者函数。
        输出示例：{'added': [(86, '    '), (87, '    def to_json_new(self, comments = True):'), (88, '        data = {'), (89, '            "name": self.node_name,')...(95, '')], 'removed': []}
        在上述示例中，PipelineEngine和AI_give_params是添加的对象，没有删除的对象。
        但是这里的添加不代表是新加入的对象，因为在git diff中，对某一行的修改在diff中是以删除和添加的方式表示的。
        所以对于修改的内容，也会表示为这个对象经过了added操作。

        如果需要明确知道某个对象是被新加入的，需要使用get_added_objs()函数。
        Args:
            diffs (list): 包含差异内容的列表。由类内部的get_file_diff()函数获取。

        Returns:
            dict: 包含添加和删除行信息的字典，格式为 {'added': set(), 'removed': set()}
        """
        changed_lines = {"added": [], "removed": []}
        line_number_current = 0
        line_number_change = 0

        for line in diffs:
            # 检测行号信息，例如 "@@ -43,33 +43,40 @@"
            line_number_info = re.match(r"@@ \-(\d+),\d+ \+(\d+),\d+ @@", line)
            if line_number_info:
                line_number_current = int(line_number_info.group(1))
                line_number_change = int(line_number_info.group(2))
                continue

            if line.startswith("+") and not line.startswith("+++"):
                changed_lines["added"].append((line_number_change, line[1:]))
                line_number_change += 1
            elif line.startswith("-") and not line.startswith("---"):
                changed_lines["removed"].append((line_number_current, line[1:]))
                line_number_current += 1
            else:
                # 对于没有变化的行，两者的行号都需要增加
                line_number_current += 1
                line_number_change += 1

        return changed_lines

    # TODO: 问题的关键在于，变更的行号分别对应于旧的函数名（即被移除的）和新的函数名（即被添加的），而当前实现还没有正确处理这一点。
    # 需要一种方式来关联变更行号与它们在变更前后的函数或类名。一种方法是在处理changed_lines之前先构建一个映射，该映射可以根据行号将变更后的名称映射回变更前的名称。
    # 然后，在identify_changes_in_structure函数中，可以使用这个映射来正确地识别出变更的结构。
    def identify_changes_in_structure(self, changed_lines, structures):
        """
        识别发生更改的函数或类的结构：遍历所有的更改行，对于每一行，它检查这一行是否在某个结构（函数或类）的开始行和结束行之间。
        如果是，那么这个结构就被认为是发生了更改的，将其名称和父级结构名称添加到结果字典 changes_in_structures 的相应集合中（取决于这一行是被添加的还是被删除的）。

        输出示例：{'added': {('PipelineAutoMatNode', None), ('to_json_new', 'PipelineAutoMatNode')}, 'removed': set()}

        Args:
            changed_lines (dict): 包含发生更改的行号的字典，{'added': [(行号，变更内容)], 'removed': [(行号，变更内容)]}
            structures (list): 接收的是get_functions_and_classes包含函数或类结构的列表，每个结构由结构类型、名称、起始行号、结束行号和父级结构名称组成。

        Returns:
            dict: 包含发生更改的结构的字典，键为更改类型，值为结构名称和父级结构名称的集合。
                可能的更改类型为'added'（新增）和'removed'（移除）。
        """
        changes_in_structures = {"added": set(), "removed": set()}
        for change_type, lines in changed_lines.items():
            for line_number, _ in lines:
                for (
                    structure_type,
                    name,
                    start_line,
                    end_line,
                    parent_structure,
                ) in structures:
                    if start_line <= line_number <= end_line:
                        changes_in_structures[change_type].add((name, parent_structure))
        return changes_in_structures
    
    def get_unstaged_mds(self):
        """
        获取仓库中未暂存的文件变更。
        """
        unstaged_files = []
        diffs = self.repo.index.diff(None)
        untracked_files = self.repo.untracked_files
        for diff in diffs + untracked_files:
            if isinstance(diff, git.Diff):
                file_path = diff.b_path
            else:
                file_path = diff
            unstaged_files.append(file_path)
        return unstaged_files
    
    def add_unstaged_mds(self):
        """
        将所有未暂存的文件添加到暂存区。
        """
        unstaged_markdown_files = self.get_unstaged_mds()
        for file_path in unstaged_markdown_files:
            add_command = f'git -C {self.repo.working_dir} add "{file_path}"'
            subprocess.run(add_command, shell=True, check=True)
        return unstaged_markdown_files
    
    
if __name__ == "__main__":

    repo_path = "/path/to/your/repo/"
    change_detector = ChangeDetector(repo_path)
    changed_files = change_detector.get_staged_pys()
    print(f"\nchanged_files:{changed_files}\n\n")
    for file_path, is_new_file in changed_files.items():
        changed_lines = change_detector.parse_diffs(
            change_detector.get_file_diff(file_path, is_new_file)
        )
        # print("changed_lines:",changed_lines)
        file_handler = FileHandler(repo_path=repo_path, file_path=file_path)
        changes_in_pyfile = change_detector.identify_changes_in_structure(
            changed_lines,
            file_handler.get_functions_and_classes(file_handler.read_file()),
        )
        print(f"Changes in {file_path} Structures:{changes_in_pyfile}\n")
