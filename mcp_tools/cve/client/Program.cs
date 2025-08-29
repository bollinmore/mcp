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
    {
        msgs.RemoveRange(0, msgs.Count - maxHistory);
    }
}

static object? GetProp(object obj, string name)
{
    var t = obj.GetType();
    // 先找屬性 (PascalCase / camelCase)
    var p = t.GetProperty(name, BindingFlags.Public | BindingFlags.Instance | BindingFlags.IgnoreCase);
    if (p != null) return p.GetValue(obj);

    // 找欄位
    var f = t.GetField(name, BindingFlags.Public | BindingFlags.Instance | BindingFlags.IgnoreCase);
    if (f != null) return f.GetValue(obj);

    return null;
}

static string ToolSchemaToString(object tool)
{
    // 常見名稱：input_schema / parameters / schema
    var candidates = new[] { "InputSchema", "input_schema", "Parameters", "parameters", "Schema", "schema" };
    foreach (var n in candidates)
    {
        var v = GetProp(tool, n);
        if (v != null)
        {
            try
            {
                if (v is JsonNode node)
                {
                    return node.ToJsonString(new JsonSerializerOptions { WriteIndented = false, MaxDepth = 256 });
                }
                if (v is JsonElement je)
                {
                    return je.GetRawText();
                }
                return JsonSerializer.Serialize(v, new JsonSerializerOptions { WriteIndented = false, MaxDepth = 256 });
            }
            catch
            {
                return v.ToString() ?? "";
            }
        }
    }

    // 找不到就保守序列化整個 tool（可能很大，必要時你可截斷）
    try
    {
        return JsonSerializer.Serialize(tool, new JsonSerializerOptions { WriteIndented = false, MaxDepth = 256 });
    }
    catch
    {
        return tool.ToString() ?? "";
    }
}

static string ToolName(object tool)
{
    return GetProp(tool, "Name")?.ToString()
        ?? GetProp(tool, "name")?.ToString()
        ?? tool.GetType().Name;
}

static string ToolDescription(object tool)
{
    return GetProp(tool, "Description")?.ToString()
        ?? GetProp(tool, "description")?.ToString()
        ?? "";
}

static void DumpPayload(
    List<ChatMessage> messages,
    IEnumerable<object> tools,
    string filePath)
{
    // 僅輸出必要欄位，避免 dump 檔自己就過肥
    var dump = new
    {
        Messages = messages.Select(m => new
        {
            Role = m.Role.ToString(),
            Text = m.Text
        }),
        Tools = tools.Select(t => new
        {
            Name = ToolName(t),
            Description = ToolDescription(t),
            InputSchema = ToolSchemaToString(t)
        })
    };

    var json = JsonSerializer.Serialize(dump, new JsonSerializerOptions
    {
        WriteIndented = true,
        MaxDepth = 256
    });

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
    .UseFunctionInvocation() // 建議先關閉，避免自動掛入額外工具定義
    .Build();

    // 2) 啟動 MCP Client（你的 CVE server）
    IMcpClient mcpClient = await McpClientFactory.CreateAsync(
        new StdioClientTransport(new()
        {
            Command = @"C:\Users\alvin.chen\Downloads\mcp-sample\h2o.cve.mcp.exe",
            Arguments = [],
            Name = "Minimal CVE MCP Server",
        }));

    // 3) 取得 MCP 工具
    Console.WriteLine("Available tools:");
    var allTools = await mcpClient.ListToolsAsync();
    foreach (var tool in allTools) Console.WriteLine(tool);
    Console.WriteLine();

    // 若仍觸發 413，請改成只挑必要那支工具：
    // var selectedTools = allTools.Where(t => ToolName(t) == "get_cve_records").Cast<object>().ToArray();
    var selectedTools = allTools.ToArray();  // allTools 應該已經是 IList<AITool>

    var messages = new List<ChatMessage>();
    const int MaxHistory = 8;

    while (true)
    {
        Console.Write("Prompt (:q 退出): ");
        var input = Console.ReadLine();
        if (input is null) continue;
        if (input.Trim().Equals(":q", StringComparison.OrdinalIgnoreCase)) break;

        messages.Add(new(ChatRole.User, input));
        TrimHistory(messages, MaxHistory);

        var options = new ChatOptions
        {
            Tools = selectedTools // 直接把 MCP 工具集合帶進去
        };

        // 發送前 dump payload
        var dumpPath = $"request_dump_{DateTime.Now:yyyyMMdd_HHmmss}.json";
        DumpPayload(messages, selectedTools.Cast<object>(), dumpPath);

        // 4) 真正送 request
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
