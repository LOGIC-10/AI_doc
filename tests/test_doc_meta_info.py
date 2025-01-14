import os
import tempfile
from pathlib import Path

import pytest

from repo_agent.doc_meta_info import find_all_referencer


@pytest.fixture
def temp_files():
    """Fixture to create temporary test files"""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield temp_dir


def create_test_files(temp_dir: str, files_content: dict):
    """Helper function to create test files with given content"""
    for file_path, content in files_content.items():
        full_path = os.path.join(temp_dir, file_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)


# @pytest.fixture
# def jedi_config():
#     """Configure Jedi for testing"""
#     import jedi
#     # 可以添加必要的 Jedi 配置
#     return {
#         "use_filesystem_cache": False,
#         "case_insensitive_completion": False
#     }


def test_find_references_in_same_file(temp_files):
    """Test finding references within the same file"""
    files = {
        "test.py": """def my_function():
    x = 5
    print(x)
    if True:
        y = x + 1
"""
    }
    create_test_files(temp_files, files)

    refs = find_all_referencer(
        repo_path=temp_files,
        variable_name="x",
        file_path="test.py",
        line_number=2,
        column_number=4,
        in_file_only=True
    )

    assert len(refs) == 2
    assert all(ref[0] == "test.py" for ref in refs)
    ref_lines = sorted(ref[1] for ref in refs)
    assert ref_lines == [3, 5]


def test_find_references_across_files(temp_files):
    """Test finding references across multiple files"""
    files = {
        "module1.py": """
def shared_function():
    return 42

x = shared_function()  # First reference
""",
        "module2.py": """
from module1 import shared_function

result = shared_function()  # Second reference
""",
        "subdir/module3.py": """
from ..module1 import shared_function

def another_func():
    return shared_function()  # Third reference
"""
    }
    create_test_files(temp_files, files)

    refs = find_all_referencer(
        repo_path=temp_files,
        variable_name="shared_function",
        file_path="module1.py",
        line_number=2,
        column_number=4,
        in_file_only=False
    )

    assert len(refs) == 5
    ref_files = sorted(ref[0] for ref in refs)
    assert ref_files == ["module1.py", "module2.py", "module2.py", "subdir/module3.py", "subdir/module3.py"]


def test_find_references_in_class(temp_files):
    """Test finding references in class methods"""
    files = {
        "test.py": """class MyClass:
    def __init__(self):
        self.value = 42
    
    def method1(self):
        print(self.value)
    
    def method2(self):
        return self.value + 1
"""
    }
    create_test_files(temp_files, files)

    refs = find_all_referencer(
        repo_path=temp_files,
        variable_name="value",
        file_path="test.py",
        line_number=3,
        column_number=13,
        in_file_only=True
    )

    assert len(refs) > 0
    assert all(ref[0] == "test.py" for ref in refs)


def test_find_references_in_nested_functions(temp_files):
    """Test finding references in nested function definitions"""
    files = {
        "test.py": """
def outer_function():
    x = 10  # Variable definition
    
    def inner_function1():
        print(x)  # First reference
        
    def inner_function2():
        y = x + 1  # Second reference
        
    inner_function1()
    inner_function2()
"""
    }
    create_test_files(temp_files, files)

    refs = find_all_referencer(
        repo_path=temp_files,
        variable_name="x",
        file_path="test.py",
        line_number=3,
        column_number=4,
        in_file_only=True
    )

    assert len(refs) == 2
    assert all(ref[0] == "test.py" for ref in refs)
    ref_lines = sorted(ref[1] for ref in refs)
    assert ref_lines == [6, 9]


def no_test_find_references_with_imports(temp_files):
    """Test finding references with different import styles"""
    files = {
        "mymodule.py": """
MY_CONSTANT = 42  # Constant definition
""",
        "direct_import.py": """
from mymodule import MY_CONSTANT
print(MY_CONSTANT)  # First reference
""",
        "star_import.py": """
from mymodule import *
print(MY_CONSTANT)  # Second reference
""",
        "module_import.py": """
import mymodule
print(mymodule.MY_CONSTANT)  # Third reference
"""
    }
    create_test_files(temp_files, files)

    refs = find_all_referencer(
        repo_path=temp_files,
        variable_name="MY_CONSTANT",
        file_path="mymodule.py",
        line_number=2,
        column_number=0,
        in_file_only=False
    )

    assert len(refs) == 3
    ref_files = sorted(ref[0] for ref in refs)
    assert ref_files == ["direct_import.py", "module_import.py", "star_import.py"]


def test_no_references(temp_files):
    """Test when there are no references to the variable"""
    files = {
        "test.py": """
def unused_function():
    pass

x = 5
"""
    }
    create_test_files(temp_files, files)

    refs = find_all_referencer(
        repo_path=temp_files,
        variable_name="unused_function",
        file_path="test.py",
        line_number=2,
        column_number=4,
        in_file_only=False
    )

    assert len(refs) == 0


def test_invalid_file(temp_files):
    """Test behavior with invalid file path"""
    refs = find_all_referencer(
        repo_path=temp_files,
        variable_name="x",
        file_path="nonexistent.py",
        line_number=1,
        column_number=1,
        in_file_only=False
    )

    assert len(refs) == 0


def test_empty_file(temp_files):
    """Test behavior with empty file"""
    files = {
        "empty.py": ""
    }
    create_test_files(temp_files, files)

    refs = find_all_referencer(
        repo_path=temp_files,
        variable_name="x",
        file_path="empty.py",
        line_number=1,
        column_number=1,
        in_file_only=True
    )

    assert len(refs) == 0


def test_with_debug_info(temp_files):
    """Test with debug information"""
    files = {
        "test.py": """x = 1
print(x)
"""
    }
    create_test_files(temp_files, files)

    refs = find_all_referencer(
        repo_path=temp_files,
        variable_name="x",
        file_path="test.py",
        line_number=1,
        column_number=0,
        in_file_only=True
    )
    
    print(f"\nDebug info:")
    print(f"References found: {refs}")
    print(f"File contents:")
    with open(os.path.join(temp_files, "test.py")) as f:
        print(f.read())
        
    assert len(refs) == 1
