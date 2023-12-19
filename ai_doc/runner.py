import os, json
from ai_doc.file_handler import FileHandler
from ai_doc.change_detector import ChangeDetector
from ai_doc.project_manager import ProjectManager
from ai_doc.chat_engine import ChatEngine
from concurrent.futures import ThreadPoolExecutor, as_completed
import yaml
import subprocess
import logging
from ai_doc.config import CONFIG


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class Runner:
    def __init__(self):
        self.project_manager = ProjectManager(repo_path=CONFIG['repo_path'],project_hierachy=CONFIG['project_hierachy']) 
        self.change_detector = ChangeDetector(repo_path=CONFIG['repo_path'])
        self.chat_engine = ChatEngine(CONFIG=CONFIG)
    
    def generate_hierachy(self):
        """
        函数的作用是为整个项目生成一个最初的全局结构信息
        """
        # 初始化一个File_handler
        file_handler = FileHandler(self.project_manager.repo_path, None)
        file_structure = file_handler.generate_overall_structure()
        json_output = file_handler.convert_structure_to_json(file_structure)

        json_file = os.path.join(CONFIG['repo_path'], CONFIG['project_hierachy'])
        # Save the JSON to a file
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(json_output, f, indent=4, ensure_ascii=False)

        # logger.info(f"JSON structure generated and saved to '{json_file}'.")

    def get_all_pys(self, directory):
        """
        获取给定目录下的所有 Python 文件。

        Args:
            directory (str): 要搜索的目录。

        Returns:
            list: 所有 Python 文件的路径列表。
        """
        python_files = []

        for root, dirs, files in os.walk(directory):
            for file in files:
                if file.endswith('.py'):
                    python_files.append(os.path.join(root, file))

        return python_files


    def git_commit(self, file_path, commit_message):
        try:
            subprocess.check_call(['git', 'add', file_path])
            subprocess.check_call(['git', 'commit', '--no-verify', '-m', commit_message])
        except subprocess.CalledProcessError as e:
            print(f'An error occurred while trying to commit {file_path}: {str(e)}')


    def run(self):
        """
        Runs the document update process.

        This method detects the changed Python files, processes each file, and updates the documents accordingly.

        Returns:
            None
        """
        # 先检查是否成功运行过 `first_generate()`.
        first_gen = not os.path.exists(os.path.join(self.project_manager.repo_path,
                                           FileHandler.remove_leading_back_slashes(CONFIG['Markdown_Docs_folder']),
                                           '.first-gen.aidoc'))
        # 再先检测是否存在全局的 project_hierachy.json 结构信息
        abs_project_hierachy_path = os.path.join(CONFIG['repo_path'], CONFIG['project_hierachy'])
        if not os.path.exists(abs_project_hierachy_path):
            self.generate_hierachy()
            logger.info(f"已生成项目全局结构信息，存储路径为: {abs_project_hierachy_path}")
    
        changed_files = self.change_detector.get_staged_pys(first_gen=first_gen)

        if len(changed_files) == 0:
            logger.info("没有检测到任何变更，不需要更新文档。")
            return
        
        else:
            logger.info(f"检测到暂存区中变更的文件：{changed_files}")

        repo_path = self.project_manager.repo_path

        for file_path, is_new_file in changed_files.items(): # 这里的file_path是相对路径

            # file_path = os.path.join(repo_path, file_path)  # 将file_path变成绝对路径
            # 判断当前python文件内容是否为空，如果为空则跳过：
            if os.path.getsize(os.path.join(repo_path, file_path)) == 0:
                continue
            # 否则，根据文件路径处理变更的文件
            self.process_file_changes(repo_path, file_path, is_new_file)
        # 生成一个标志文件, 表示本函数已经运行完一次了.
        with open(os.path.join(self.project_manager.repo_path,
                                           FileHandler.remove_leading_back_slashes(CONFIG['Markdown_Docs_folder']),
                                           '.first-gen.aidoc'), 'w'):
            pass

        # 将标志文件（未暂存）添加到暂存区
        # TODO: 目测这个 add_unstaged_mds() 有BUG. 以后再改. 改后可能会影响到此处.
        git_add_result = self.change_detector.add_unstaged_mds()

        if len(git_add_result) > 0:
            logger.info(f'已添加 {[file for file in git_add_result]} 到暂存区')


    def add_new_item(self, file_handler, json_data):
        new_item = {}
        new_item["file_path"] = file_handler.file_path
        new_item["objects"] = []
        # 因为是新增的项目，所以这个文件里的所有对象都要写一个文档
        for structure_type, name, start_line, end_line, parent in file_handler.get_functions_and_classes(file_handler.read_file()):
            code_info = file_handler.get_obj_code_info(structure_type, name, start_line, end_line, parent)
            md_content = self.chat_engine.generate_doc(code_info, file_handler)
            code_info["md_content"] = md_content
            new_item["objects"].append(code_info)

        json_data["files"].append(new_item)
        # 将新的项写入json文件
        with open(self.project_manager.project_hierachy, 'w') as f:
            json.dump(json_data, f, indent=4, ensure_ascii=False)
        logger.info(f"已将新增文件 {file_handler.file_path} 的结构信息写入json文件。")
        # 将变更部分的json文件内容转换成markdown内容
        markdown = file_handler.convert_to_markdown_file(file_path=file_handler.file_path)
        # 将markdown内容写入.md文件
        file_handler.write_file(os.path.join(self.project_manager.repo_path, CONFIG['Markdown_Docs_folder'], file_handler.file_path.replace('.py', '.md')), markdown)
        logger.info(f"已生成新增文件 {file_handler.file_path} 的Markdown文档。")

    
    def process_file_changes(self, repo_path, file_path, is_new_file):
        """
        函数将在检测到的变更文件的循环中被调用，作用是根据文件绝对路径处理变更的文件，包括新增的文件和已存在的文件。
        其中，changes_in_pyfile是一个字典，包含了发生变更的结构的信息，示例格式为：{'added': {'add_context_stack', '__init__'}, 'removed': set()}

        Args:
            repo_path (str): The path to the repository.
            file_path (str): The relative path to the file.
            is_new_file (bool): Indicates whether the file is new or not.

        Returns:
            None
        """
        file_handler = FileHandler(repo_path=repo_path, file_path=file_path) # 变更文件的操作器
        # 获取整个py文件的代码
        source_code = file_handler.read_file()
        changed_lines = self.change_detector.parse_diffs(self.change_detector.get_file_diff(file_path, is_new_file))
        changes_in_pyfile = self.change_detector.identify_changes_in_structure(changed_lines, file_handler.get_functions_and_classes(source_code))
        logger.info(f"检测到变更对象：\n{changes_in_pyfile}")
        
        # 判断project_hierachy.json文件中能否找到对应.py文件路径的项
        with open(self.project_manager.project_hierachy, 'r') as f:
            json_data = json.load(f)
        
        # 标记是否找到了对应的文件
        found = False
        for i, file in enumerate(json_data["files"]):

            if file["file_path"] == file_handler.file_path: # 找到了对应文件
                # 更新json文件中的内容
                json_data["files"][i] = self.update_existing_item(file, file_handler, changes_in_pyfile)
                # 将更新后的file写回到json文件中
                with open(self.project_manager.project_hierachy, 'w') as f:
                    json.dump(json_data, f, indent=4, ensure_ascii=False)
                
                logger.info(f"已更新{file_handler.file_path}文件的json结构信息。")

                found = True

                # 将变更部分的json文件内容转换成markdown内容
                markdown = file_handler.convert_to_markdown_file(file_path=file_handler.file_path)
                # 将markdown内容写入.md文件
                file_handler.write_file(os.path.join(self.project_manager.repo_path, CONFIG['Markdown_Docs_folder'], file_handler.file_path.replace('.py', '.md')), markdown)
                logger.info(f"已更新{file_handler.file_path}文件的Markdown文档。")
                break

        # 如果没有找到对应的文件，就添加一个新的项
        if not found:
            self.add_new_item(file_handler,json_data)

        # 将run过程中更新的Markdown文件（未暂存）添加到暂存区
        git_add_result = self.change_detector.add_unstaged_mds()
        
        if len(git_add_result) > 0:
            logger.info(f'已添加 {[file for file in git_add_result]} 到暂存区') 

        

    def update_existing_item(self, file, file_handler, changes_in_pyfile):
        
        new_obj, del_obj = self.get_new_objects(file_handler)

        # 处理被删除的对象
        for obj_name in del_obj: # 真正被删除的对象
            for file_obj in file["objects"]:
                if file_obj["name"] == obj_name:
                    file["objects"].remove(file_obj)
                    logger.info(f"已删除 {obj_name} 对象。")
                    break

        referencer_list = []
        file_structure_result = file_handler.generate_file_structure(file["file_path"]) # 生成文件的结构信息

        # file_structure_result返回的是：
        # {
        #     "file_path": file_path,
        #     "objects": json_objects
        # }

        new_objects = file_structure_result["objects"] # 获得当前文件中的所有对象， 这里其实就是当前文件更新之后的结构了
        new_info_dict = {obj["name"]: obj for obj in new_objects}

        # 先更新全局文件结构信息，比如代码起始行\终止行等
        # Explain:
        # 只要一个文件中的某一个对象发生了变更，这个文件中的其他对象的code_info内容（除了md_content）都需要改变
        # 依靠再次识别这个文件的代码，更新其他对象的code_start_line等等可能被影响到的字段信息
        for obj in file["objects"]:
            if obj["name"] in new_info_dict:
                new_info = new_info_dict[obj["name"]]
                obj["type"] = new_info["type"]
                obj["code_start_line"] = new_info["code_start_line"]
                obj["code_end_line"] = new_info["code_end_line"]
                obj["parent"] = new_info["parent"]
                obj["name_column"] = new_info["name_column"]

        # 对于每一个对象：获取其引用者列表
        for obj_name, _ in changes_in_pyfile['added']:

            for new_object in new_objects: # 引入new_objects的目的是获取到find_all_referencer中必要的参数信息。在changes_in_pyfile['added']中只有对象和其父级结构的名称，缺少其他参数
                if obj_name == new_object["name"]:  # 确保只有当added中的对象名称匹配new_objects时才添加引用者
                    # 获取每个需要生成文档的对象的引用者
                    referencer_obj = {
                        "obj_name": obj_name,
                        "obj_referencer_list": self.project_manager.find_all_referencer(
                            variable_name=new_object["name"],
                            file_path=file["file_path"],
                            line_number=new_object["code_start_line"],
                            column_number=new_object["name_column"]
                        )
                    }
                    referencer_list.append(referencer_obj) # 对于每一个正在处理的对象，添加他的引用者字典到全部对象的应用者列表中
        

        with ThreadPoolExecutor(max_workers=5) as executor:
            # 通过线程池并发执行
            futures = []
            for changed_obj in changes_in_pyfile['added']: # 对于每一个待处理的对象
                for ref_obj in referencer_list:
                    if changed_obj[0] == ref_obj["obj_name"]: # 在referencer_list中找到它的引用者字典！
                        future = executor.submit(self.update_object, file, file_handler, changed_obj[0], ref_obj["obj_referencer_list"])
                        logger.info(f"正在生成 {file_handler.file_path}中的{changed_obj[0]} 对象文档...")
                        futures.append(future)

            for future in futures:
                future.result()

        # 更新传入的file参数
        return file
    

    def update_object(self, file, file_handler, obj_name, obj_referencer_list):


        for obj in file["objects"]: # file["objects"]保存的是原先的旧的对象信息

            if obj["name"] == obj_name: # obj_name标识了在added中需要生成文档的对象（也就是发生了变更的对象）

                response_message = self.chat_engine.generate_doc(obj, file_handler, obj_referencer_list)
                obj["md_content"] = response_message.content
                break



    def get_new_objects(self, file_handler):
        """
        函数通过比较当前版本和上一个版本的.py文件，获取新增和删除的对象

        Args:
            file_handler (FileHandler): 文件处理器对象。
        Returns:
            tuple: 包含新增和删除对象的元组，格式为 (new_obj, del_obj)
        输出示例：
        new_obj: ['add_context_stack', '__init__']
        del_obj: []
        """
        current_version, previous_version = file_handler.get_modified_file_versions()
        parse_current_py = file_handler.get_functions_and_classes(current_version)
        parse_previous_py = file_handler.get_functions_and_classes(previous_version) if previous_version else []

        current_obj = {f[1] for f in parse_current_py}
        previous_obj = {f[1] for f in parse_previous_py}

        new_obj = list(current_obj - previous_obj)
        del_obj = list(previous_obj - current_obj)

        return new_obj, del_obj


if __name__ == "__main__":

    runner = Runner()
    
    runner.run()

    logger.info("文档任务完成。")

