"""idf.py 启动器：绕过 idf.py 对 MSYSTEM 环境的检测（检测到即拒绝执行 main）。
用法：python run_idf.py <idf.py 参数...>
例：  python run_idf.py set-target esp32s3
      python run_idf.py build
      python run_idf.py -p COM5 flash monitor
"""
import os
import sys
import runpy

os.environ.pop('MSYSTEM', None)
os.environ.pop('MSYSTEM_CARCH', None)
os.environ.pop('MSYSTEM_CHOST', None)
os.environ.pop('MSYSTEM_PREFIX', None)
# 沙箱环境缺 PROCESSOR_ARCHITECTURE，platform.machine() 会返回空，
# idf_tools.py 推导出 "Windows-" 这种非法平台名而直接报错
os.environ.setdefault('PROCESSOR_ARCHITECTURE', 'AMD64')

IDF_TOOLS = r'D:\gongju\Espressif'
os.environ.setdefault('IDF_PATH', IDF_TOOLS + r'\frameworks\esp-idf-v5.4.4')
os.environ.setdefault('IDF_TOOLS_PATH', IDF_TOOLS)
os.environ.setdefault('IDF_PYTHON_ENV_PATH', IDF_TOOLS + r'\python_env\idf5.4_py3.11_env')

TOOLS_DIR = os.path.join(os.environ['IDF_PATH'], 'tools')
sys.path.insert(0, TOOLS_DIR)
sys.argv = ['idf.py'] + sys.argv[1:]

runpy.run_path(os.path.join(TOOLS_DIR, 'idf.py'), run_name='__main__')
