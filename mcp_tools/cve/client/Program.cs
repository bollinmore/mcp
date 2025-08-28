using Azure.AI.OpenAI;
using Azure;
using Microsoft.Extensions.AI;
using ModelContextProtocol.Client;

// Create an IChatClient using Azure OpenAI.
// 從環境變數 GITHUB_AI_PAT 讀取 personal access token
string personalAccessToken = Environment.GetEnvironmentVariable("GITHUB_AI_PAT") ?? throw new InvalidOperationException("GITHUB_AI_PAT 環境變數未設定");
IChatClient client =
    new ChatClientBuilder(
        new AzureOpenAIClient(
            new Uri("https://models.inference.ai.azure.com"),
            new AzureKeyCredential(personalAccessToken))
        .GetChatClient("gpt-4o").AsIChatClient())
    .UseFunctionInvocation()
    .Build();

// Create the MCP client
// Configure it to start and connect to your MCP server.
IMcpClient mcpClient = await McpClientFactory.CreateAsync(
    new StdioClientTransport(new()
    {
        Command = @"C:\Users\alvin.chen\Downloads\mcp-sample\h2o.cve.mcp.exe",
        Arguments = [],
        Name = "Minimal CVE MCP Server",
    }));

// List all available tools from the MCP server.
Console.WriteLine("Available tools:");
IList<McpClientTool> tools = await mcpClient.ListToolsAsync();
foreach (McpClientTool tool in tools)
{
    Console.WriteLine($"{tool}");
}
Console.WriteLine();

// Conversational loop that can utilize the tools via prompts.
List<ChatMessage> messages = [];
while (true)
{
    Console.Write("Prompt: ");
    messages.Add(new(ChatRole.User, Console.ReadLine()));

    List<ChatResponseUpdate> updates = [];
    await foreach (ChatResponseUpdate update in client
        .GetStreamingResponseAsync(messages, new() { Tools = [.. tools] }))
    {
        Console.Write(update);
        updates.Add(update);
    }
    Console.WriteLine();

    messages.AddMessages(updates);
}