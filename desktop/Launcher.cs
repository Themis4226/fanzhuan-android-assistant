using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Management;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace FanzhuanDesktop {
    static class Program {
        public static readonly string Root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
        public static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
        [STAThread] static int Main(string[] args) {
            Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
            string name;
            using (var h = SHA256.Create()) name = BitConverter.ToString(h.ComputeHash(Encoding.UTF8.GetBytes(Root.ToLowerInvariant()))).Replace("-", "");
            bool first;
            using (var gate = new Mutex(true, @"Local\FanzhuanDesktop_" + name, out first)) {
                if (!first) { MessageBox.Show("这个项目的启动窗口已经打开。", "翻转外语助手"); return 1; }
                try {
                    var form = new MainWindow();
                    if (args.Length == 2 && args[0] == "--self-test") form.TestDirectory = Path.GetFullPath(args[1]);
                    Application.Run(form); return form.TestFailed ? 1 : 0;
                } catch (Exception e) { MessageBox.Show(e.Message, "启动失败"); return 1; }
            }
        }
        public static string Python {
            get {
                foreach (var relative in new[] { @"runtime\python.exe", @".venv\Scripts\python.exe" }) {
                    string p = Path.Combine(Root, relative); if (File.Exists(p)) return p;
                }
                throw new InvalidOperationException("没有找到项目 Python。请完整解压 Windows 环境包，并保留 exe、runtime、tools 和脚本在同一目录。");
            }
        }
        // Windows argv quoting. No cmd.exe/PowerShell shell interprets these arguments.
        public static string Quote(string arg) {
            var s = new StringBuilder("\""); int slashes = 0;
            foreach (char c in arg) {
                if (c == '\\') { slashes++; continue; }
                if (c == '"') { s.Append('\\', slashes * 2 + 1); s.Append(c); }
                else { s.Append('\\', slashes); s.Append(c); }
                slashes = 0;
            }
            s.Append('\\', slashes * 2); s.Append('"'); return s.ToString();
        }
        public static ProcessStartInfo PythonInfo(string[] args) {
            var info = new ProcessStartInfo(Python, String.Join(" ", new[] { "-X", "utf8", "-u" }.Concat(args).Select(Quote)));
            info.WorkingDirectory = Root; info.UseShellExecute = false; info.CreateNoWindow = true;
            info.RedirectStandardOutput = true; info.RedirectStandardError = true;
            info.StandardOutputEncoding = Encoding.UTF8; info.StandardErrorEncoding = Encoding.UTF8;
            info.EnvironmentVariables["PYTHONUTF8"] = "1"; info.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";
            return info;
        }
        public static string Query(params string[] args) {
            using (var p = new Process()) {
                p.StartInfo = PythonInfo(args); p.Start();
                var errors = p.StandardError.ReadToEndAsync(); var output = p.StandardOutput.ReadToEndAsync();
                if (!p.WaitForExit(10000)) { p.Kill(); throw new InvalidOperationException("读取本地状态超时。"); }
                if (p.ExitCode != 0) throw new InvalidOperationException(errors.Result);
                return output.Result.Trim();
            }
        }
    }

    sealed class SessionDialog : Form {
        public CheckBox Answers = new CheckBox { Text = "授权本次会话自动答题和提交", AutoSize = true };
        public CheckBox Paid = new CheckBox { Text = "允许 DeepSeek 付费调用（仅发送题目 OCR 文字）", AutoSize = true };
        public NumericUpDown Limit = new NumericUpDown { Minimum = 0, Maximum = 100, Value = 0, Enabled = false, Width = 95 };
        TextBox headers = new TextBox { Width = 480, Height = 70, Multiline = true, ScrollBars = ScrollBars.Vertical };
        public SessionDialog() {
            Text = "新建本地会话"; Size = new Size(550, 400); FormBorderStyle = FormBorderStyle.FixedDialog;
            StartPosition = FormStartPosition.CenterParent; MaximizeBox = false; MinimizeBox = false;
            Font = new Font("Microsoft YaHei UI", 10);
            var panel = new FlowLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(18), FlowDirection = FlowDirection.TopDown, WrapContents = false };
            panel.Controls.Add(new Label { Text = "默认不授权答题、不调用付费 API。", AutoSize = true, Margin = new Padding(0, 0, 0, 16) });
            panel.Controls.Add(Answers); panel.Controls.Add(Paid);
            var row = new FlowLayoutPanel { AutoSize = true }; row.Controls.Add(new Label { Text = "API 次数上限", AutoSize = true, Padding = new Padding(0, 5, 0, 0) }); row.Controls.Add(Limit); panel.Controls.Add(row);
            panel.Controls.Add(new Label { Text = "允许直接选项的语法/阅读标题（可选，每行一个）", AutoSize = true, Margin = new Padding(0, 12, 0, 4) });
            panel.Controls.Add(headers);
            var ok = new Button { Text = "创建会话", Size = new Size(120, 36), Margin = new Padding(0, 14, 0, 0) };
            Paid.CheckedChanged += delegate { Limit.Enabled = Paid.Checked; if (!Paid.Checked) Limit.Value = 0; };
            ok.Click += delegate {
                if ((Paid.Checked || headers.Text.Trim().Length > 0) && !Answers.Checked) { MessageBox.Show("请先明确授权本次自动答题和提交。", Text); return; }
                if (Paid.Checked && Limit.Value == 0) { MessageBox.Show("启用付费调用时，请填写大于0的次数上限。", Text); return; }
                DialogResult = DialogResult.OK; Close();
            };
            panel.Controls.Add(ok); Controls.Add(panel);
        }
        public string[] Arguments() {
            var args = new List<string> { "session_init.py" };
            if (Answers.Checked) args.Add("--authorize-answers");
            if (Paid.Checked) args.AddRange(new[] { "--allow-api", "--api-call-limit", Limit.Value.ToString() });
            foreach (var h in headers.Lines.Select(x => x.Trim()).Where(x => x.Length > 0)) args.AddRange(new[] { "--arbitrary-choice-header", h });
            return args.ToArray();
        }
    }

    sealed class MainWindow : Form {
        Label session = new Label { AutoSize = false, Height = 48, Dock = DockStyle.Fill };
        Label state = new Label { Text = "就绪", AutoSize = true, Padding = new Padding(0, 7, 0, 0) };
        TextBox log = new TextBox { Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Both, WordWrap = false, Dock = DockStyle.Fill, BackColor = Color.FromArgb(248, 250, 252), Font = new Font("Consolas", 10) };
        ComboBox mode = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Width = 175 };
        NumericUpDown count = new NumericUpDown { Minimum = 1, Maximum = 50, Value = 50, Width = 75 };
        TextBox header = new TextBox { Width = 260 };
        Button start = new Button(), stop = new Button(), newSession = new Button(), closeSession = new Button();
        CheckBox chainWords = new CheckBox { Text = "完成后继续下一个单词卡包", Checked = true, AutoSize = true, Padding = new Padding(10, 6, 0, 0) };
        List<Button> jobButtons = new List<Button>();
        Process worker; bool stopping; StreamWriter logFile; string lastLog = "";
        string[] secrets; Dictionary<string, object> metadata;
        readonly string[] taskScripts = { "daily_word_tasks.py", "daily_words.py", "daily_listening.py", "daily_choice_pages.py", "daily_spell.py", "daily_navigate.py", "daily_suggest.py", "assistant_step.py", "session_init.py" };
        public string TestDirectory; public bool TestFailed;
        System.Windows.Forms.Timer testTimer; int testStage; bool testLineSeen;
        public bool Busy { get { return worker != null; } }
        public MainWindow() {
            Text = "翻转外语助手 · 桌面启动器"; Size = new Size(1000, 760); MinimumSize = new Size(900, 680);
            StartPosition = FormStartPosition.CenterScreen; BackColor = Color.White; Font = new Font("Microsoft YaHei UI", 10);
            secrets = LoadSecrets();
            var page = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(22), ColumnCount = 1, RowCount = 7 };
            foreach (var h in new[] { 45, 45, 55, 50, 48, 52 }) page.RowStyles.Add(new RowStyle(SizeType.Absolute, h));
            page.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            page.Controls.Add(new Label { Text = "翻转外语助手", Font = new Font("Microsoft YaHei UI", 21, FontStyle.Bold), ForeColor = Color.FromArgb(0, 120, 110), AutoSize = true }, 0, 0);
            page.Controls.Add(new Label { Text = "先在手机打开对应练习页，再开始批次。日志直接显示在窗口内。", AutoSize = true, ForeColor = Color.DimGray, Padding = new Padding(0, 10, 0, 0) }, 0, 1);
            var tools = Row();
            AddJob(tools, "检查连接", new[] { "phone_capture.py", "doctor" });
            AddJob(tools, "截图查看", new[] { "phone_capture.py", "capture", "--open" });
            AddJob(tools, "读取文字", new[] { "assistant_step.py", "--live" });
            AddButton(tools, "手机投屏", delegate { Mirror(); });
            AddButton(tools, "运行记录", delegate { OpenFolder(Path.Combine(Program.Root, "runs")); });
            AddJob(tools, "离线测试", new[] { "-m", "unittest", "discover", "-s", "tests", "-v" });
            page.Controls.Add(tools, 0, 2);
            var sessions = Row(); sessions.Controls.Add(session); session.Width = 450; session.Dock = DockStyle.None;
            newSession = AddButton(sessions, "新建会话", delegate { NewSession(); });
            closeSession = AddButton(sessions, "结束会话", delegate {
                if (!OtherWorker()) RunJob("结束本地会话", new[] { "desktop_helper.py", "--close-session" });
            });
            AddButton(sessions, "刷新状态", delegate { RefreshSession(); }); page.Controls.Add(sessions, 0, 3);
            var options = Row(); mode.Items.AddRange(new object[] { "单词卡包", "听力播放", "语法 / 阅读选项" }); mode.SelectedIndex = 0;
            options.Controls.Add(mode); options.Controls.Add(new Label { Text = "最多步数", AutoSize = true, Padding = new Padding(4, 6, 0, 0) }); options.Controls.Add(count);
            options.Controls.Add(new Label { Text = "页面标题", AutoSize = true, Padding = new Padding(12, 6, 0, 0) }); options.Controls.Add(header);
            mode.SelectedIndexChanged += delegate { count.Maximum = mode.SelectedIndex == 0 ? 50 : mode.SelectedIndex == 1 ? 30 : 20; header.Enabled = mode.SelectedIndex != 0; chainWords.Visible = mode.SelectedIndex == 0; };
            header.Enabled = false; page.Controls.Add(options, 0, 4);
            var actions = Row(); start = AddButton(actions, "开始运行", delegate { StartTask(); }); start.BackColor = Color.FromArgb(0, 130, 118); start.ForeColor = Color.White;
            stop = AddButton(actions, "停止当前批次", delegate { StopJob(); }); stop.Enabled = false;
            actions.Controls.Add(state); actions.Controls.Add(chainWords); page.Controls.Add(actions, 0, 5); page.Controls.Add(log, 0, 6); Controls.Add(page);
            Shown += delegate { RefreshSession(); if (TestDirectory != null) BeginTests(); };
            FormClosing += delegate { if (Busy) StopJob(); };
            FormClosed += delegate { if (logFile != null) logFile.Dispose(); };
        }
        static FlowLayoutPanel Row() { return new FlowLayoutPanel { Dock = DockStyle.Fill, WrapContents = false, Padding = new Padding(0, 3, 0, 0) }; }
        Button AddButton(Control row, string text, Action action) {
            var b = new Button { Text = text, AutoSize = true, Height = 34, MinimumSize = new Size(100, 34), Margin = new Padding(0, 0, 9, 0), FlatStyle = FlatStyle.Flat };
            b.Click += delegate { try { action(); } catch (Exception e) { Append(e.Message); state.Text = "操作未完成，查看日志"; } }; row.Controls.Add(b); return b;
        }
        void AddJob(Control row, string label, string[] args) { jobButtons.Add(AddButton(row, label, delegate { RunJob(label, args); })); }
        string[] LoadSecrets() {
            var values = new List<string>(); string env = Environment.GetEnvironmentVariable("DEEPSEEK_API_KEY");
            if (!String.IsNullOrWhiteSpace(env)) values.Add(env.Trim());
            string file = Path.Combine(Program.Root, ".env.local");
            if (File.Exists(file)) foreach (string line in File.ReadAllLines(file, Encoding.UTF8)) {
                if (line.TrimStart().StartsWith("DEEPSEEK_API_KEY=")) { string key = line.Substring(line.IndexOf('=') + 1).Trim().Trim('"', '\''); if (key.Length > 0) values.Add(key); }
            }
            return values.ToArray();
        }
        string Clean(string text) {
            foreach (string key in secrets) text = text.Replace(key, "[密钥已隐藏]");
            return Regex.Replace(text, @"\bsk-[A-Za-z0-9_-]{12,}", "[密钥已隐藏]");
        }
        void Append(string text) {
            if (String.IsNullOrEmpty(text) || IsDisposed) return;
            if (InvokeRequired) { try { BeginInvoke(new Action<string>(Append), text); } catch (InvalidOperationException) { } return; }
            text = Clean(text); if (text.Contains("桌面中文测试 OK")) testLineSeen = true;
            if (log.TextLength > 200000) log.Clear(); log.AppendText(text + Environment.NewLine);
            if (logFile != null) { logFile.WriteLine(text); logFile.Flush(); }
        }
        bool OtherWorker() {
            using (var query = new ManagementObjectSearcher("SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name='python.exe' OR Name='pythonw.exe'"))
            foreach (ManagementObject item in query.Get()) {
                string command = Convert.ToString(item["CommandLine"]);
                if (taskScripts.Any(x => command.IndexOf(x, StringComparison.OrdinalIgnoreCase) >= 0)) { Append("另一个手机任务脚本正在运行，请先停止它，再开始新批次。"); return true; }
            }
            return false;
        }
        void RefreshSession() {
            if (Busy) return;
            try {
                metadata = Program.Json.Deserialize<Dictionary<string, object>>(Program.Query("desktop_helper.py", "--status"));
                string s = Convert.ToString(metadata["status"]); bool active = s == "active";
                session.Text = active ? String.Format("会话进行中 · 自动答题{0} · API 剩余 {1}/{2} 次", Convert.ToBoolean(metadata["authorized"]) ? "已授权" : "未授权", metadata["remaining"], metadata["api_limit"]) : "没有进行中的会话 · 新会话默认 API 额度为0";
                newSession.Enabled = !active; closeSession.Enabled = active;
            } catch (Exception e) { metadata = null; session.Text = "会话状态读取失败，详见日志"; Append(e.Message); }
        }
        void NewSession() {
            if (Busy || OtherWorker()) return;
            using (var dialog = new SessionDialog()) if (dialog.ShowDialog(this) == DialogResult.OK) RunJob("创建本地会话", dialog.Arguments());
        }
        void StartTask() {
            if (Busy) return; RefreshSession();
            if (metadata == null || Convert.ToString(metadata["status"]) != "active" || !Convert.ToBoolean(metadata["authorized"])) { Append("请先创建已授权自动答题的本地会话。不会自动新增调用额度。"); return; }
            var args = new List<string>();
            if (mode.SelectedIndex == 0) {
                if (chainWords.Checked) args.AddRange(new[] { "daily_word_tasks.py", "--count", count.Value.ToString(), "--max-cards", "6" });
                else args.AddRange(new[] { "daily_words.py", "--skip-review", "--count", count.Value.ToString() });
            }
            else {
                if (String.IsNullOrWhiteSpace(header.Text)) { Append("请填写手机当前练习页的实际标题。"); return; }
                args.AddRange(new[] { mode.SelectedIndex == 1 ? "daily_listening.py" : "daily_choice_pages.py", "--header", header.Text.Trim(), "--count", count.Value.ToString() });
                if (mode.SelectedIndex == 2) args.Add("--allow-info-and-scroll");
            }
            RunJob("运行" + mode.Text, args.ToArray());
        }
        void SetBusy(bool busy) {
            start.Enabled = !busy; stop.Enabled = busy; newSession.Enabled = !busy; closeSession.Enabled = !busy;
            mode.Enabled = !busy; count.Enabled = !busy; header.Enabled = !busy && mode.SelectedIndex != 0;
            chainWords.Enabled = !busy;
            foreach (var b in jobButtons) b.Enabled = !busy;
        }
        void RunJob(string label, string[] args) {
            if (Busy || OtherWorker()) return;
            string folder = Path.Combine(Program.Root, "runs", DateTime.Now.ToString("yyyyMMdd_HHmmss_fffffff") + "_desktop"); Directory.CreateDirectory(folder);
            lastLog = Path.Combine(folder, "desktop.log"); if (logFile != null) logFile.Dispose(); logFile = new StreamWriter(lastLog, false, new UTF8Encoding(false));
            var p = new Process(); p.StartInfo = Program.PythonInfo(args); p.EnableRaisingEvents = true;
            p.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { Append(e.Data); };
            p.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { Append(e.Data); };
            p.Exited += delegate {
                p.WaitForExit();
                if (!IsDisposed) try { BeginInvoke(new Action(delegate { Finish(p); })); } catch (InvalidOperationException) { }
            };
            stopping = false; worker = p;
            try { p.Start(); SetBusy(true); state.Text = label + "…"; Append("开始：" + label); p.BeginOutputReadLine(); p.BeginErrorReadLine(); }
            catch { worker = null; p.Dispose(); SetBusy(false); throw; }
        }
        void Finish(Process p) {
            if (worker != p) return;
            int code = p.ExitCode; worker = null; p.Dispose(); SetBusy(false);
            state.Text = stopping ? "批次已停止" : code == 0 ? "本次进程已结束" : "脚本已暂停，请查看日志";
            Append(stopping ? "已停止。请核对手机当前页面；不会自动重试。" : "进程退出码：" + code + "。任务是否完成以手机反馈为准。");
            RefreshSession();
        }
        void StopJob() {
            if (!Busy) return; stopping = true; stop.Enabled = false;
            if (!worker.HasExited) {
                var info = new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "taskkill.exe"), "/PID " + worker.Id + " /T /F");
                info.UseShellExecute = false; info.CreateNoWindow = true; info.RedirectStandardOutput = true; info.RedirectStandardError = true;
                using (var kill = Process.Start(info)) { kill.WaitForExit(5000); }
            }
        }
        void Mirror() {
            string dir = Path.Combine(Program.Root, @"tools\scrcpy-win64-v5.0.1"); string exe = Path.Combine(dir, "scrcpy.exe");
            if (!File.Exists(exe)) { Append("没有找到 scrcpy，请完整解压环境包。"); return; }
            var info = new ProcessStartInfo(exe, "--select-usb --no-audio --no-clipboard-autosync --no-control --no-power-on --window-title Fanzhuan-Phone-View");
            info.UseShellExecute = false; info.CreateNoWindow = true; info.WorkingDirectory = dir; info.EnvironmentVariables["ADB"] = Path.Combine(dir, "adb.exe");
            Process.Start(info); Append("已请求打开只读投屏；手机需先允许 USB 调试。");
        }
        void OpenFolder(string path) { Directory.CreateDirectory(path); Process.Start(new ProcessStartInfo("explorer.exe", Program.Quote(path)) { UseShellExecute = false }); }
        void Require(bool condition, string reason) { if (!condition) throw new InvalidOperationException("自检失败：" + reason); }
        void BeginTests() {
            Directory.CreateDirectory(TestDirectory);
            try {
                var tricky = new[] { "中文 含空格", "", "quote\"value", @"C:\tail\", "$(no-shell)&literal" };
                var actual = Program.Json.Deserialize<string[]>(Program.Query(new[] { "desktop_helper.py", "--echo-args" }.Concat(tricky).ToArray()));
                Require(actual.SequenceEqual(tricky), "参数转义 / Unicode");
                using (var d = new SessionDialog()) { Require(!d.Answers.Checked && !d.Paid.Checked && d.Limit.Value == 0, "默认授权与费用"); }
                Require(Clean("sk-1234567890123456").IndexOf("1234567890123456") < 0, "日志密钥隐藏");
                Require(chainWords.Checked && taskScripts.Contains("daily_word_tasks.py"), "单词自动衔接默认入口");
                testStage = 0; RunJob("桌面离线自检", new[] { "-c", "import time;print('桌面中文测试 OK',flush=True);time.sleep(.3)" });
                Require(Busy && stop.Enabled && !start.Enabled, "运行按钮互斥");
                testTimer = new System.Windows.Forms.Timer { Interval = 200 };
                testTimer.Tick += delegate { TestTick(); }; testTimer.Start();
            } catch (Exception e) { EndTests(e); }
        }
        void TestTick() {
            try {
                if (testStage == 0 && !Busy) {
                    Require(testLineSeen, "后台进程中文输出"); testStage = 1;
                    RunJob("停止按钮自检", new[] { "-c", "import time;time.sleep(20)" });
                } else if (testStage == 1) { testStage = 2; StopJob(); }
                else if (testStage == 2 && !Busy) {
                    Require(start.Enabled && !stop.Enabled && state.Text == "批次已停止", "停止与按钮恢复");
                    using (var bmp = new Bitmap(Width, Height)) { DrawToBitmap(bmp, new Rectangle(0, 0, Width, Height)); bmp.Save(Path.Combine(TestDirectory, "desktop_preview.png")); }
                    EndTests(null);
                }
            } catch (Exception e) { EndTests(e); }
        }
        void EndTests(Exception error) {
            if (testTimer != null) testTimer.Stop(); TestFailed = error != null;
            var report = new Dictionary<string, object> { { "status", error == null ? "passed" : "failed" }, { "phone_actions", 0 }, { "api_calls", 0 },
                { "checks", new[] { "Unicode and Windows argv", "zero-fee defaults", "secret redaction", "hidden process output", "stop process tree", "busy controls", "word chain default", "window rendering" } } };
            if (error != null) report["error"] = Clean(error.Message);
            File.WriteAllText(Path.Combine(TestDirectory, "desktop_validation.json"), Program.Json.Serialize(report), new UTF8Encoding(false)); Close();
        }
    }
}
