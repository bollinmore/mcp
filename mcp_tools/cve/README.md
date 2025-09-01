# MCP sample - CVE

## Sequence digram

```mermaid
sequenceDiagram
    participant User
    participant Main as Main Program
    participant LLM as Azure OpenAI (LLM)
    participant MCP as MCP Client
    participant CVE as CVE MCP Server

    Main->>Main: 讀取 GITHUB_AI_PAT 環境變數
    Main->>LLM: 初始化 ChatClient<br/>(GitHub Models endpoint)
    Main->>MCP: 創建 MCP Client<br/>(StdioClientTransport)
    MCP->>CVE: 啟動 CVE Server 進程
    
    Main->>MCP: ListToolsAsync()
    MCP->>CVE: 請求工具清單
    CVE-->>MCP: 返回工具清單
    MCP-->>Main: 返回 allTools
    
    Main->>Main: BuildToolCatalogJson(allTools)
    Main->>Main: UpsertToolCatalogSystemMessage()<br/>將工具目錄插入 messages[0]
    
    Note over MCP: 註冊 NotificationReceived 事件處理
    
    loop REPL 迴圈
        User->>Main: 輸入提示詞
        alt 輸入 :q
            Main->>Main: 結束程式
        else 一般輸入
            Main->>Main: 將 User 訊息加入 messages
            Main->>Main: TrimHistory() 限制歷史長度
            Main->>Main: DumpPayload() 儲存請求到檔案
            
            Main->>LLM: GetStreamingResponseAsync(messages)<br/>(不帶 Tools 參數)
            loop 串流回應
                LLM-->>Main: ChatResponseUpdate
                Main->>User: 即時顯示回應文字
            end
            
            Main->>Main: 將 Assistant 回應加入 messages
            Main->>Main: TrimHistory() 限制歷史長度
        end
    end
    
    Note over MCP,CVE: 平行處理：工具更新通知
    CVE-->>MCP: tools/list_changed 通知
    MCP-->>Main: 觸發 NotificationReceived 事件
    Main->>MCP: ListToolsAsync() 重新取得工具
    MCP->>CVE: 請求工具清單
    CVE-->>MCP: 返回更新的工具清單
    MCP-->>Main: 返回 refreshed tools
    Main->>Main: BuildToolCatalogJson(refreshed)
    Main->>Main: UpsertToolCatalogSystemMessage()<br/>更新 System 訊息中的工具目錄
```

## Client

1. Apply **Personal Access Tokens (Classic)** in the GitHub.
2. Set the environment variable **GITHUB_AI_PAT** to the secret token:
```bash
export GITHUB_AI_PAT="<Secret>"
```
3. Run the client with `dotnet run`. The client will list available tools, then prompt the user for input.

```
dotnet run
Available tools:
get_cve_summary
get_cve_projects
get_cve_list2
download_cve_solution
get_cve_groups2
get_cve_vender_list
get_cvss_vector
get_cve_severity
get_cve_info
is_cve_downloadable
get_cve_groups
get_cvss_score
get_cve_vender_list2
get_cve_solutions
get_cve_project_versions
get_cve_relationships
get_cve_list

Prompt:  Input you prompt...
```



## Server