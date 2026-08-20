// Driving the broker from a .NET desktop application (WPF / WinForms / console).
//
// The desktop app never implements OAuth itself. It spawns the broker, reads
// one line of JSON, and gets a token. Nothing here is Python-specific beyond
// the executable name.
//
// Build note: this is a reference snippet, not a compiled project. Drop the
// M365Broker class into your solution.

using System;
using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading.Tasks;

namespace DesktopApp.Auth
{
    public sealed class AuthResult
    {
        [JsonPropertyName("ok")]           public bool Ok { get; set; }
        [JsonPropertyName("access_token")] public string AccessToken { get; set; }
        [JsonPropertyName("token_type")]   public string TokenType { get; set; }
        [JsonPropertyName("expires_in")]   public int ExpiresIn { get; set; }
        [JsonPropertyName("expires_at")]   public double ExpiresAt { get; set; }
        [JsonPropertyName("scope")]        public string Scope { get; set; }
        [JsonPropertyName("account")]      public Account Account { get; set; }

        // Only present on failure.
        [JsonPropertyName("error")]        public string Error { get; set; }
        [JsonPropertyName("message")]      public string Message { get; set; }
    }

    public sealed class Account
    {
        [JsonPropertyName("name")]     public string Name { get; set; }
        [JsonPropertyName("username")] public string Username { get; set; }
        [JsonPropertyName("oid")]      public string ObjectId { get; set; }
        [JsonPropertyName("tid")]      public string TenantId { get; set; }
    }

    public class BrokerException : Exception
    {
        public string Code { get; }
        public BrokerException(string code, string message) : base(message) => Code = code;
    }

    public sealed class M365Broker
    {
        private readonly string _executable;
        private readonly string _clientId;

        public M365Broker(string clientId, string executable = "m365-auth")
        {
            _clientId = clientId;
            _executable = executable;
        }

        /// <summary>Interactive sign-in. Opens the user's browser. Call once.</summary>
        public Task<AuthResult> LoginAsync() => RunAsync("login", "--json");

        /// <summary>Show the account picker even if a session exists.</summary>
        public Task<AuthResult> SwitchAccountAsync() => RunAsync("login", "--json", "--force");

        /// <summary>
        /// A currently valid access token, silently refreshed if needed.
        /// Call this before each batch of Graph work - never cache the string
        /// yourself, or you will eventually use an expired one.
        /// </summary>
        public async Task<string> GetAccessTokenAsync()
        {
            var result = await RunAsync("token", "--json");
            return result.AccessToken;
        }

        public Task<AuthResult> LogoutAsync() => RunAsync("logout", "--json");

        private async Task<AuthResult> RunAsync(params string[] args)
        {
            var info = new ProcessStartInfo
            {
                FileName = _executable,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            foreach (var arg in args) info.ArgumentList.Add(arg);
            info.ArgumentList.Add("--client-id");
            info.ArgumentList.Add(_clientId);

            using var process = Process.Start(info);

            // Read both streams before waiting: a full pipe buffer deadlocks
            // the child otherwise. stdout is the JSON, stderr is progress text
            // you can surface in the UI ("Waiting for sign-in...").
            var stdoutTask = process.StandardOutput.ReadToEndAsync();
            var stderrTask = process.StandardError.ReadToEndAsync();
            await Task.WhenAll(stdoutTask, stderrTask);
            await process.WaitForExitAsync();

            var stdout = stdoutTask.Result.Trim();
            if (string.IsNullOrEmpty(stdout))
                throw new BrokerException("no_output", stderrTask.Result.Trim());

            var result = JsonSerializer.Deserialize<AuthResult>(stdout);
            if (!result.Ok)
                throw new BrokerException(result.Error, result.Message);

            return result;
        }
    }

    internal static class Demo
    {
        public static async Task Main()
        {
            var broker = new M365Broker("11111111-2222-3333-4444-555555555555");

            var session = await broker.LoginAsync();
            Console.WriteLine($"Signed in as {session.Account.Username}");

            // Now call Microsoft Graph directly with the token.
            var token = await broker.GetAccessTokenAsync();
            using var http = new System.Net.Http.HttpClient();
            http.DefaultRequestHeaders.Authorization =
                new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", token);

            var inbox = await http.GetStringAsync(
                "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$top=5");
            Console.WriteLine(inbox);
        }
    }
}
