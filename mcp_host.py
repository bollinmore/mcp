import subprocess
import time
import sys

# 啟動 MCP Server
server_process = subprocess.Popen([sys.executable, 'mcp_server.py'])
time.sleep(1)  # 等待 server 啟動

# 執行 MCP Client
subprocess.run([sys.executable, 'mcp_client.py'])

# 結束 server
server_process.terminate()
