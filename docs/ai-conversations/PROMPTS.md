# 主要提示词汇总

按时间顺序记录我在本项目中对 AI 提出的提示词。完整对话见同目录按日期归档的纪要。

## 2026-09-08

| # | 时间 | 提示词 |
|---|---|---|
| 1 | 13:40 | 我用ollama在终端下载了qwen3.8，我不知道他在哪个盘里面，我想把他放到d盘 |
| 2 | 13:53 | 所以是qwen3.8吗 |
| 3 | 14:02 | 是qwen3.8 8b吗 |
| 4 | 14:03 | AI交互课第1周任务，大家可提前完成。没有VPS可以直接使用自己的电脑当作服务器。开发板可直接使用上学期的任何带IMU传感器的开发板。@image#1:81662e859f989dd312ae00c663d9d139.png |
| 5 | 14:09 | MPU6050是什么 |
| 6 | 14:26 | 任务大概是要干什么，我没看懂 |
| 7 | 15:47 | 继续，推进，硬件用esp32，先不用硬件 |
| 8 | 15:47 | 继续，推进，硬件用esp32，先不用硬件 |
| 9 | 16:04 | 停止运行 |
| 10 | 16:05 | 我本地的qwen3.8怎么用 |
| 11 | 16:10 | 你告诉我怎么用，我自己测试 |
| 12 | 16:13 | 停止测试 |
| 13 | 16:13 | 你告诉我怎么用，我自己测试 |
| 14 | 16:15 | C:\Users\user>ollama run qwen3.8<br>pulling manifest<br>pulling ac3714bfddde:  37% ▕█████████████████████                                     ▏ 343 MB/931 MB  2.9 MB/s   3m22s^C<br>C:\Users\user>为什么在下载 |
| 15 | 16:18 | [出现错误 2147942632 (0x800700e8) (启动“"C:\Users\user\.workbuddy\binaries\node\versions\22.22.2-2\node.exe" D:\gongju\workbuddy\resources\app.asar.unpacked\cli\vendor\shim\safe-delete-bulk-guard.cjs check --target C:\Users\user\.ollama\models”时)]<br>你先告诉我现在是什么情况 |
| 16 | 16:24 | 我用ollama在终端下载了qwen3.8，我不知道他在哪个盘里面，我想把他放到d盘 |
| 17 | 16:24 | C:\Users\user>ollama run qwen3.8<br>>>> 你好<br>Thinking...<br>用户简单地说了一句"你好"，这是一个中文问候。我应该用友好、自然的方式回应，用中文回复，并询问是否需要帮助。<br>...done thinking.<br>你好！👋 很高兴见到你。有什么我可以帮你的吗？无论是回答问题、写东西、翻译、编程，还是随便聊聊，都可以随时告诉我。 |
| 18 | 16:27 | 但是他的上下文是不是太少了 |
| 19 | 16:31 | 他为什么不可以访问我的电脑文件 |
| 20 | 16:32 | 他为什么不可以访问我的电脑文件，你先回答，不要直接动手 |
| 21 | 16:34 | 但他不是本地模型吗，可以编辑文件也没什么影响吧 |
| 22 | 16:48 | 你下载的不会是云端的吧，为什么gpu不跑 |
| 23 | 16:57 | 上下文真的就只有4096吗？ |
| 24 | 17:04 | 有32k吗，如果换成32会有什么影响 |
| 25 | 17:04 | 请继续完成未完成的任务。 |
| 26 | 17:05 | 我用ollama在终端下载了qwen3.8，我不知道他在哪个盘里面，我想把他放到d盘 |
| 27 | 17:05 | 请继续完成未完成的任务。 |
| 28 | 17:10 | 你来完成，还有他没办法直接写代码 |
| 29 | 17:12 | 好，还有现在上下文多少 |
| 30 | 17:16 | 你> 帮我写个网页版贪吃蛇<br>他没有弹出什么 |
| 31 | 17:17 | 你不要写我就是测试一下 |
| 32 | 17:22 | 不用这个agent了，删了吧，我测试一下qwen3.8有没有32k |
| 33 | 17:26 | 我用ollama在终端下载了qwen3.8，我不知道他在哪个盘里面，我想把他放到d盘 |
| 34 | 17:26 | 所以这个是qwen3还是qwen3.8？ |
| 35 | 17:30 | ollama pull smtek/Qwen3.8-27B:Q2_K_XL-12gb这是什么 |
| 36 | 17:35 | C:\Users\user>ollama ps<br>NAME              ID              SIZE     PROCESSOR          CONTEXT    UNTIL<br>qwen3.8:latest    22130167c4c2    18 GB    53%/47% CPU/GPU    4096       4 minutes from now |
| 37 | 17:36 | C:\Users\user>ollama run qwen3.8<br>>>> >>>/set parameter num_ctx 32768<br>Thinking...<br>The user is trying to use a command syntax like `/set parameter num_ctx 32768` which appears to be a prompt<br>injection or attempt to modify system parameters. This look … |
| 38 | 17:36 | C:\Users\user>ollama run qwen3.8<br>>>> >>>/set parameter num_ctx 32768<br>Thinking...<br>The user is trying to use a command syntax like `/set parameter num_ctx 32768` which appears to be a prompt<br>injection or attempt to modify system parameters. This look … |
| 39 | 17:37 | >>>/set parameter num_ctx 32768<br>Set parameter 'num_ctx' to '32768'<br>>>> |
| 40 | 17:39 | 为什么gpu不跑呢 |
| 41 | 17:41 | 最高上下文可以多少 |
| 42 | 18:57 | 第一周任务完成了多少进度 |
| 43 | 19:00 | 第一周任务完成了多少进度 |
| 44 | 19:16 | @image#1:Clipboard_Screenshot.png 为什么下载这么慢 |
| 45 | 19:18 | @image#1:Clipboard_Screenshot.png |
| 46 | 19:19 | 下载的这个你可以查到吗 |
| 47 | 19:21 | 下载的这个你可以查到吗 |
| 48 | 19:30 | 选b，我有几个问题这个跟原版有什么区别，还有我已经有了qwen3.8本地的会不会冲突 |
| 49 | 19:35 | D:\ollama>ollama pull smtek/Qwen3.8-27B:Q2_K_XL-12gb<br>pulling manifest<br>pulling dd04d13a3811:  83% ▕███████████████████████████████████████████████           ▏ 8.8 GB/ 10 GB  196 KB/s   2h38m^C这个我中段了，删了吧 |
| 50 | 19:38 | qwen3.8-32k这个是什么 |
| 51 | 19:44 | 下多少了 |
| 52 | 19:53 | 下多少了 |
| 53 | 20:09 | 请继续完成未完成的任务。 |
| 54 | 20:16 | C:\Users\user>ollama run qwen3.8ollama run qwen3.8-32k<br>Error: pull model manifest: file does not exist这是什么问题，先告诉我 |

## 2026-09-09

| # | 时间 | 提示词 |
|---|---|---|
| 1 | 10:08 | 我现在已经插上硬件了，是esp32-s3-eye |
| 2 | 10:12 | 我用ollama在终端下载了qwen3.8，我不知道他在哪个盘里面，我想把他放到d盘 |
| 3 | 10:47 | <conversation_history_summary><br>Summary of the conversation between an AI agent and a user.<br>All tasks described below are already completed.<br>**DO NOT re-run, re-do or re-execute any of these tasks!**<br>Use this summary only for context understanding.< … |
| 4 | 10:45 | 请继续执行任务 |
| 5 | 10:54 | 431<br>密码：88888888 |
| 6 | 12:35 | 请继续执行任务 |
| 7 | 12:41 | 请继续执行任务 |
| 8 | 12:50 | 后台任务暂停 |
| 9 | 13:09 | 好的，现在我要自己看一下，网页看看能不能看到数据 |
| 10 | 13:10 | @image#1:Clipboard_Screenshot.png |
| 11 | 13:12 | 角速度呢 |
| 12 | 15:12 | @image#1:3e726c414d86eecce0519c48de7252fc.png 这个任务现在完成多少了 |
| 13 | 15:13 | 好，继续推进 |
| 14 | 15:14 | 先整理一下D:\aijiaohu这个文件夹 |
| 15 | 15:19 | qwen3.8呢 |
| 16 | 15:20 | 网页版，无法连接服务 |
| 17 | 15:23 | 可以，没有VPS可以直接使用自己的电脑当作服务器 |
| 18 | 15:25 | 后台任务停止运行 |
| 19 | 15:29 | 这个服务器是什么原理 |
| 20 | 15:33 | 这个服务器有什么优缺点 |
| 21 | 15:43 | 如果你你你会怎么改进这个东西，先说不用做 |
| 22 | 15:45 | 我的意思是往后可以推进什么功能，可以干什么，这些有什么用 |
| 23 | 15:51 | ai交互这个可以，具体要怎么做，是把本地模型放到esp32里面吗？还是说用服务器来实现大模型跟esp32 |
| 24 | 15:53 | 但是大模型跟esp32交互有什么用，esp32可以交流吗 |
| 25 | 15:58 | 请继续执行任务 |
| 26 | 15:58 | 但是这样，不是需要个软件吗，但是这样子esp32不会崩溃吗，运行内存够吗，我前面就想给esp32多一点功能但是直接崩溃了，你有没有什么解决方法 |
| 27 | 16:01 | 但是这样，不是需要个软件吗，但是这样子esp32不会崩溃吗，运行内存够吗，我前面就想给esp32多一点功能但是直接崩溃了，你有没有什么解决方法 |
| 28 | 16:09 | 先说不要做 |
| 29 | 16:14 | 先说不要做 |
| 30 | 16:17 | 他这个板子都有什么功能 |

## 2026-09-10

| # | 时间 | 提示词 |
|---|---|---|
| 1 | 13:07 | ESP32-S3-EYE开发板官方原理图写的IMU传感器是QMA7981，但在ESP官方的组件库中仅找到了qma6100p这个组件库，没有QMA7981这个组件库，开发板上IMU传感器到底是QMA7981还是QMA6100P？怎么确定？ |
| 2 | 13:16 | 你自己测试一下，还有这个网站上可以出现摄像头实时吗 |
| 3 | 13:35 | 请继续执行任务 |
| 4 | 13:35 | 我已经拔掉板子的 USB 线，等 2 秒再插回去。后台任务在干什么 |

## 2026-09-11

| # | 时间 | 提示词 |
|---|---|---|
| 1 | 09:22 | 可以远程把这个数据给传输到电脑上面吗，先回答 |
| 2 | 09:30 | 这个先等一下，先把摄像头的实时内容传到网页上面 |
| 3 | 10:21 | <conversation_history_summary><br>Summary of the conversation between an AI agent and a user.<br>All tasks described below are already completed.<br>**DO NOT re-run, re-do or re-execute any of these tasks!**<br>Use this summary only for context understanding.< … |
| 4 | 10:20 | 为什么摄像头上面显示未连接 |
| 5 | 10:22 | 为什么摄像头上面显示未连接，还有就是有时候会显示未更新 |
| 6 | 10:24 | 我刚刚打开网页看了，发现可以用 |
| 7 | 10:24 | 但是现在网页也不显示摄像头的内容了 |
| 8 | 11:11 | 摄像头现在不会显示实时了，可以走服务器吗，因为后面我要拿出去也可以把数据上传到这个网页 |
| 9 | 11:31 | 为什么摄像头上面显示未连接 |
| 10 | 11:31 | 现在摄像头的内容是结果服务器吗 |
| 11 | 12:00 | 后台任务停止 |

## 2026-09-14

| # | 时间 | 提示词 |
|---|---|---|
| 1 | 12:48 | 他是通过什么来传输到服务器上面的 |
| 2 | 12:49 | 他是通过什么来传输到服务器上面的 |
| 3 | 12:52 | 为什么摄像头上面显示未连接 |
| 4 | 12:52 | 如果我带着这个板子到外面，网页上面还会有数据跟摄像头画面吗 |
| 5 | 13:03 | 单元1 最小交互闭环与设备反馈（第1—3周）#<br>目标与学时　目标1、2、6；理论4学时、实践8学时。<br>任务与成果　自主构建单源采集—VPS—Web系统；在Web发出重新采集请求并追踪设备执行；增加实体按键和本地/远端反馈。<br>关键知识与技能　器件与连接核对、驱动复用、传感数据及单位、上传与存储、VPS部署、Web状态、请求关联、设备回执和实体反馈。<br>重点与难点　数据值、来源与采集时间对应；刷新存储记录与触发新采集的区别；服务器受理、设备接收、完成与超时的证据。<br>实施步骤　第1周：构建开发 … |
| 6 | 13:05 | 完成这些需要什么 |
| 7 | 13:08 | 现在已经插上了，我现在就想知道接下来要做什么 |
| 8 | 13:11 | 为什么摄像头上面显示未连接 |
| 9 | 13:11 | 我想知道原理怎么做的，我想从头开始一步一步来 |
| 10 | 13:13 | 看不懂这是什么意思 |
| 11 | 13:15 | 道理我都懂，但是我要怎么做，写成你这样 |
| 12 | 13:17 | 我想重走一遍，前面我都是让你写的，我都不知道是怎么做的 |
| 13 | 13:27 | <conversation_history_summary><br>Summary of the conversation between an AI agent and a user.<br>All tasks described below are already completed.<br>**DO NOT re-run, re-run or re-do or re-execute any of these tasks!**<br>Use this summary only for context under … |
| 14 | 13:26 | 现在在干嘛 |
| 15 | 13:33 | 我的意思是你给我看代码让我了解每一步干什么，就不用重新写了 |
| 16 | 13:56 | 现在在干嘛 |
| 17 | 13:56 | 所以你刚刚在干什么，前面不就可以跑了吗，你是重新写了一次？ |
| 18 | 13:58 | 为什么还要我自己来烧录，你来在ESP-IDF 5.4 CMD里面 |
| 19 | 14:20 | 现在在干嘛 |
| 20 | 14:20 | 你在干什么 |
| 21 | 14:22 | 现在是干什么 |
| 22 | 14:24 | 是我没有放平，没事了 |
| 23 | 14:27 | 整理一下文件夹 |
| 24 | 14:32 | 网页无法连接服务器 |
| 25 | 14:42 | 角速度可以用上吗 |
| 26 | 14:45 | 把他移除吧，优化一下网页 |
| 27 | 14:52 | 网页上面加上温度，内存 |
| 28 | 16:23 | 现在在干嘛 |
| 29 | 16:23 | 剩余内存是不是应该写上占用多少，剩余多少，还有单位换成mb |
| 30 | 16:33 | @image#1:Clipboard_Screenshot.png 这里删了，还有记录数据可以下载 |
| 31 | 16:36 | 可以选择下近几天这样的或者近百几条 |
| 32 | 16:39 | 如果记录超过上限把前面最早的给覆盖掉 |
| 33 | 16:39 | 如果记录超过上限把前面最早的给删了 |
| 34 | 16:46 | 为什么我没有动加速角还会变 |
| 35 | 16:53 | 现在在干嘛 |
| 36 | 16:53 | 所以他这个3维是什么值，为什么写加速角 |
| 37 | 16:58 | 为什么这么不稳定 |
| 38 | 17:00 | 我是的是数据更新，怎么时不时断开 |

## 2026-09-18

| # | 时间 | 提示词 |
|---|---|---|
| 1 | 09:28 | <conversation_history_summary><br>Summary of the conversation between an AI agent and a user.<br>All tasks described below are already completed.<br>**DO NOT re-run, re-do or re-execute any of the tasks mentioned!**<br>Use this summary only for context underst … |
| 2 | 09:28 | 考核需要检查你的github仓库，仓库中应保留从学期初开始到学期结束的完整提交记录。包括与AI的对话、主要提示词、项目代码、技术及设计相关的各种文档。 |
| 3 | 09:39 | 我有git |
| 4 | 09:48 | 为什么一直在删东西？ |
| 5 | 09:50 | 什么意思 |
| 6 | 09:50 | 好，继续推进 |
| 7 | 09:55 | 你现在就按照最好的方式推进 |
| 8 | 10:00 | 帮我提交 |
| 9 | 10:00 | 帮我提交，记得新建一个仓库 |
| 10 | 10:47 | 项目目标：持续采集设备的IMU、声音、图像、定位等传感器数据到服务器，形成一个数据链，设计多种传感器数据融合方案和模型网络结构，训练并评估模型效果，设备端模型和服务端模型协同工作，为用户提供主动的AI服务。对应这门课的目标，研究人与AI之间交互的技术实现，用户体验反馈并优化迭代。<br>初级：采集至少1-2个传感器数据，实现数据融合与模型训练，部署并测试人与AI交互的效果；<br>中级：将传感器类别增加到3-4种或更多；<br>高级：更高质量的工程实现，更低功耗，更快的响应，更好的鲁棒性。<br>然后完成第2周 |
| 11 | 11:28 | 上传到GitHub |
| 12 | 11:32 | 为什么我的GitHub上面会有两个 @image#1:Clipboard_Screenshot.png |
| 13 | 11:33 | 现在没有了 |
| 14 | 11:34 | 现在没有了，继续推进，把这次推进的在原仓库拉一条分支 |
| 15 | 11:38 | 先不用上传了，我先测试一下第2周的结果 |
| 16 | 11:40 | 第2周是什么任务，你完成了什么 |
| 17 | 11:41 | 后台任务停止运行 |

## 2026-09-20

| # | 时间 | 提示词 |
|---|---|---|
| 1 | 12:55 | 你是怎么上传到github上面的 |
| 2 | 12:57 | 我要告诉另一个ai，你帮我说 |
| 3 | 13:35 | 第2周任务全部完成了吗 |
| 4 | 13:37 | 做真实浏览器实测 |
| 5 | 13:38 | 做真实浏览器实测，VPS先用电脑代替 |
| 6 | 13:45 | 所以这跟第一周的有什么变化 |
| 7 | 13:53 | @image#1:Clipboard_Screenshot.png 这些是什么 |
| 8 | 13:56 | <task-notification><br><task-id>qvhM3f</task-id><br><tool-use-id>call_01_U66cT66WqyStZdKiTEIC6611</tool-use-id><br><status>completed</status><br><summary>Background command &quot;find ~/.workbuddy -maxdepth 3 -iname &quot;*.jsonl&quot; 2&gt;/dev/null \| head - … |
| 9 | 13:56 | 不是只有4个吗 |
| 10 | 13:57 | 我删了4个，还有1个在哪里 |
| 11 | 14:01 | 好的，现在第2周完成了，可以拉一条分支上传吗，然后别人打开这个github里面显示是第2周，分支里面可以选第2第1周 |
| 12 | 14:15 | md文件单元1是什么，一之2是什么意思，为什么分支有3个 |
| 13 | 14:36 | 继续推进第3周任务 |

## 2026-09-23

| # | 时间 | 提示词 |
|---|---|---|
| 1 | 09:20 | @image#1:bedaadcda72f9c4ab0e4e4767d011066.png |
| 2 | 09:23 | 这些都没完成你为什么前面说完成了 |
| 3 | 09:35 | <task-notification><br><task-id>qvhM3f</task-id><br><tool-use-id>call_01_U66cT66WqyStZdKiTEIC6611</tool-use-id><br><status>completed</status><br><summary>Background command &quot;find ~/.workbuddy -maxdepth 3 -iname &quot;*.jsonl&quot; 2&gt;/dev/null \| head - … |
