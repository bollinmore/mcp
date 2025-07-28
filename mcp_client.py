import socket

def send_request(message, host='127.0.0.1', port=5000):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((host, port))
        s.sendall(message.encode())
        data = s.recv(1024)
        print('Server:', data.decode())

if __name__ == "__main__":
    print("歡迎使用 MCP Client！輸入 'exit' 結束對話。")
    while True:
        user_input = input("你: ")
        if user_input.strip().lower() == 'exit':
            print("結束對話。")
            break
        send_request(user_input)
