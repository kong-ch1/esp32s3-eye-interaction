# 注册一个组件，源码是 main.c。INCLUDE_DIRS 让 main.c 能找到同目录的头文件
idf_component_register(SRCS "main.c"
                    INCLUDE_DIRS ".")
