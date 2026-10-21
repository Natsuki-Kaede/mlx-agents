/** Slash commands typed at the start of the input box, as in Claude Code and Codex. */
export type SlashCommand = { name: string; args?: string; description: string };

export const COMMANDS: SlashCommand[] = [
  { name: "goal", args: "目标和完成标准", description: "设定目标：agent 自主循环执行，直到完成并验证" },
  { name: "review", description: "检视工作目录里未提交的改动，按严重程度列出问题" },
  { name: "init", description: "了解工作目录里的项目，生成 AGENTS.md 项目说明" },
  { name: "auto", description: "开关自动批准（写文件、运行命令不再询问）" },
  { name: "new", description: "开始新对话" },
];

/** What /review and /init ask the agent to do. */
export const COMMAND_PROMPTS: Record<string, string> = {
  review:
    "检视工作目录里还没提交的改动（先用 git status 和 git diff 查看）。以代码审查的角度，优先找出 bug、风险、行为回归和缺少的测试，按严重程度列出，每条附上文件和行号；没发现问题就明确说明，并指出剩下的风险或测试缺口。只检视，不要修改文件。",
  init:
    "了解工作目录里的项目（结构、技术栈、构建 / 测试 / 运行命令、代码约定），然后在工作目录根目录写 AGENTS.md，给以后在这个项目里工作的 agent 看：项目概览、目录结构、常用命令、代码风格和约定、提交前要跑的检查。只写从项目里确认过的事实，具体、简洁。已有 AGENTS.md 就在它的基础上更新。",
};

/** The command a message invokes, if it starts with a known one ("/goal 写个爬虫" → goal, "写个爬虫"). */
export function parseCommand(text: string): { name: string; arg: string } | null {
  const match = /^\/([a-z]+)(?:\s+([\s\S]*))?$/.exec(text.trim());
  if (!match || !COMMANDS.some((c) => c.name === match[1])) return null;
  return { name: match[1], arg: (match[2] ?? "").trim() };
}

/** Commands to offer while the user is typing a command name ("/" or "/re"). */
export function commandsFor(text: string): SlashCommand[] {
  const match = /^\/([a-z]*)$/.exec(text);
  return match ? COMMANDS.filter((c) => c.name.startsWith(match[1])) : [];
}
