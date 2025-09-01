// Program.cs
// dotnet add package Microsoft.Extensions.AI
// dotnet add package Azure.AI.OpenAI
// dotnet add package ModelContextProtocol.Client

using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Reflection;
using Azure;
using Azure.AI.OpenAI;
using Microsoft.Extensions.AI;
using ModelContextProtocol.Client;

static void TrimHistory(List<ChatMessage> msgs, int maxHistory)
{
    if (msgs.Count > maxHistory)
        msgs.RemoveRange(0, msgs.Count - maxHistory);
}

static object? GetProp(object obj, string name)
{
    var t = obj.GetType();
    var p = t.GetProperty(name, BindingFlags.Public | BindingFlags.Instance | BindingFlags.IgnoreCase);
    if (p != null) return p.GetValue(obj);
    var f = t.GetField(name, BindingFlags.Public | BindingFlags.Instance | BindingFlags.IgnoreCase);
    if (f != null) return f.GetValue(obj);
    return null;
}

static string ToolSchemaToString(object tool)
{
    var candidates = new[] { "InputSchema", "input_schema", "Parameters", "parameters", "Schema", "schema" };
    foreach (var n in candidates)
    {
        var v = GetProp(tool, n);
        if (v != null)
        {
            try
            {
                if (v is JsonNode node) return node.ToJsonString(new JsonSerializerOptions { WriteIndented = false, MaxDepth = 256 });
                if (v is JsonElement je) return je.GetRawText();
                return JsonSerializer.Serialize(v, new JsonSerializerOptions { WriteIndented = false, MaxDepth = 256 });
            }
            catch
            {
                return v.ToString() ?? "";
            }
        }
    }
    try
    {
        return JsonSerializer.Serialize(tool, new JsonSerializerOptions { WriteIndented = false, MaxDepth = 256 });
    }
    catch
    {
        return tool.ToString() ?? "";
    }
}

static string ToolName(object tool) =>
    GetProp(tool, "Name")?.ToString()
    ?? GetProp(tool, "name")?.ToString()
    ?? tool.GetType().Name;

static string ToolDescription(object tool) =>
    GetProp(tool, "Description")?.ToString()
    ?? GetProp(tool, "description")?.ToString()
    ?? "";

static string BuildToolCatalogJson(IEnumerable<object> tools)
{
    var compact = tools.Select(t => new
    {
        name = ToolName(t),
        description = ToolDescription(t),
        input_schema = ToolSchemaToString(t)
    });
    return JsonSerializer.Serialize(new { mcp_tools = compact }, new JsonSerializerOptions { WriteIndented = false, MaxDepth = 256 });
}

static void DumpPayload(List<ChatMessage> messages, string filePath)
{
    var dump = new
    {
        Messages = messages.Select(m => new { Role = m.Role.ToString(), Text = m.Text })
    };
    var json = JsonSerializer.Serialize(dump, new JsonSerializerOptions { WriteIndented = true, MaxDepth = 256 });
    File.WriteAllText(filePath, json, Encoding.UTF8);
    Console.WriteLine($"[DEBUG] Payload dumped to {filePath}, bytes={Encoding.UTF8.GetByteCount(json)}");
}

try
{
    // 1) 初始化 LLM（GitHub Models / Azure endpoint）
    string personalAccessToken = Environment.GetEnvironmentVariable("GITHUB_AI_PAT")
        ?? throw new InvalidOperationException("GITHUB_AI_PAT 環境變數未設定");

    var baseClient = new AzureOpenAIClient(
        new Uri("https://models.inference.ai.azure.com"),
        new AzureKeyCredential(personalAccessToken));

    IChatClient client = new ChatClientBuilder(
        baseClient.GetChatClient("gpt-4.1-mini").AsIChatClient()
    )
    // 這裡保留 UseFunctionInvocation，以便未來如要啟用工具可行；
    // 但**本回合**我們不在 ChatOptions 傳遞 Tools，避免每回合帶入整包 schema。
    .UseFunctionInvocation()
    .Build();

    // 2) 啟動 MCP Client（你的 CVE server）
    IMcpClient mcpClient = await McpClientFactory.CreateAsync(
        new StdioClientTransport(new()
        {
            Command = @"C:\Users\alvin.chen\Downloads\mcp-sample\h2o.cve.mcp.exe",
            Arguments = [],
            Name = "Minimal CVE MCP Server",
        }));

    // 3) 取得 MCP 工具，並「一次性」以 System 訊息同步給 LLM
    Console.WriteLine("Available tools from MCP:");
    var allTools = await mcpClient.ListToolsAsync();
    foreach (var tool in allTools) Console.WriteLine(tool);
    Console.WriteLine();

    // 建立對話歷史，將工具清單以 System 訊息注入（僅作為知識，不是每回合傳 Tools 給 ChatOptions）
    var messages = new List<ChatMessage>();
    const int MaxHistory = 8;

    string currentToolCatalogJson = BuildToolCatalogJson(allTools.Cast<object>());
    var toolCatalogSystemText =
        "You can call external MCP tools via the host. " +
        "Here is the current tool catalog (names, descriptions, and JSON Schemas). " +
        "If you intend to call a tool, respond with a clear tool name and JSON arguments instead of reprinting the schema.\n" +
        currentToolCatalogJson;

    // 保留此 System 訊息位置，後續若工具更新就替換
    int toolCatalogSystemIndex = -1;
    void UpsertToolCatalogSystemMessage(string catalogJson)
    {
        var text =
            "You can call external MCP tools via the host. " +
            "Here is the current tool catalog (names, descriptions, and JSON Schemas). " +
            "If you intend to call a tool, respond with a clear tool name and JSON arguments instead of reprinting the schema.\n" +
            catalogJson;

        if (toolCatalogSystemIndex >= 0 && toolCatalogSystemIndex < messages.Count && messages[toolCatalogSystemIndex].Role == ChatRole.System)
        {
            messages[toolCatalogSystemIndex] = new(ChatRole.System, text);
        }
        else
        {
            messages.Insert(0, new(ChatRole.System, text));
            toolCatalogSystemIndex = 0;
        }
    }
    UpsertToolCatalogSystemMessage(currentToolCatalogJson);

    // 4) 監聽 MCP 的工具更新通知，收到就重新同步 System 訊息（不在每回合傳 Tools）
    //    方法名以 "tools/list_changed" 為範例，請依你的 Server 實際通知方法調整。
    mcpClient.NotificationReceived += async (_, e) =>
    {
        try
        {
            if (string.Equals(e.Method, "tools/list_changed", StringComparison.OrdinalIgnoreCase) ||
                string.Equals(e.Method, "tools/updated", StringComparison.OrdinalIgnoreCase))
            {
                Console.WriteLine("[MCP] Tools update notification received. Refreshing tool catalog...");
                var refreshed = await mcpClient.ListToolsAsync();
                currentToolCatalogJson = BuildToolCatalogJson(refreshed.Cast<object>());
                UpsertToolCatalogSystemMessage(currentToolCatalogJson);
                Console.WriteLine("[MCP] Tool catalog synced to LLM via System message.");
            }
        }
        catch (Exception ex)
        {
            Console.WriteLine("[MCP] Failed to refresh tools after notification: " + ex.Message);
        }
    };

    // 5) REPL：使用者輸入時，不要在 ChatOptions 帶 Tools（避免每次都傳大型 schema）
    while (true)
    {
        Console.Write("Prompt (:q 退出): ");
        var input = Console.ReadLine();
        if (input is null) continue;
        if (input.Trim().Equals(":q", StringComparison.OrdinalIgnoreCase)) break;

        messages.Add(new(ChatRole.User, input));
        TrimHistory(messages, MaxHistory);

        // 僅傳 messages；不附帶 Tools
        var options = new ChatOptions
        {
            // Tools = null // 預設即不帶，保留說明
        };

        // 發送前 dump（只 dump 訊息，避免工具清單巨量）
        var dumpPath = $"request_dump_{DateTime.Now:yyyyMMdd_HHmmss}.json";
        DumpPayload(messages, dumpPath);

        // 6) 串流回覆
        var updates = new List<ChatResponseUpdate>();
        await foreach (var update in client.GetStreamingResponseAsync(messages, options))
        {
            if (!string.IsNullOrWhiteSpace(update.Text))
            {
                Console.Write(update.Text);
            }
            updates.Add(update);
        }
        Console.WriteLine();

        var finalText = string.Concat(updates.Select(u => u.Text ?? string.Empty));
        messages.Add(new(ChatRole.Assistant, finalText));
        TrimHistory(messages, MaxHistory);
    }
}
catch (Exception ex)
{
    Console.WriteLine("Error: " + ex);
}
