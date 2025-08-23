import socket
import threading

def handle_client(conn, addr):
    print(f"Connected by {addr}")
    import datetime
    while True:
        data = conn.recv(1024)
        if not data:
            break
        text = data.decode().strip()
        # 簡易自然語言指令解析
        if text.lower() in ["hi", "hello", "你好"]:
            response = "你好！有什麼可以幫忙的？"
        elif text.lower().startswith("計算"):
            expr = text[2:].strip()
            try:
                result = eval(expr, {"__builtins__": None}, {})
                response = f"計算結果: {result}"
            except Exception as e:
                response = f"算式錯誤: {e}"
        elif "時間" in text:
            now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            response = f"目前時間: {now}"
        else:
            response = f"收到: {text}（暫不支援此任務）"
        conn.sendall(response.encode())
    conn.close()

def start_server(host='127.0.0.1', port=8888):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, port))
        s.listen()
        print(f"MCP Server listening on {host}:{port}")
        while True:
            conn, addr = s.accept()
            threading.Thread(target=handle_client, args=(conn, addr)).start()

if __name__ == "__main__":
    start_server()
