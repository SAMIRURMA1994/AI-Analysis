const API_BASE_URL = "http://127.0.0.1:8000";
const app = document.getElementById("app");

const state = {
  loggedIn: sessionStorage.getItem("orbit_logged_in") === "true",
  username: sessionStorage.getItem("orbit_username") || "",
  view: "home",
  messages: [],
  loading: false,
  agentMenuOpen: false,
  capabilities: [],
};

const agents = [
  { id: "github", server: "GitHub", icon: "⌘", name: "GitHub", description: "Repositories, issues, branches and pull requests.", status: "Active", active: true, tools: [] },
  { id: "playwright", server: "Playwright", icon: "◉", name: "Playwright", description: "Navigate websites, inspect pages and automate browsers.", status: "Connecting", active: false, tools: [] },
  { id: "mysql", server: "MySQL", icon: "▦", name: "MySQL", description: "Inspect databases, tables, schemas and read-only data.", status: "Connecting", active: false, tools: [] },
  { id: "local-search", server: "Local Search", icon: "⌕", name: "Local Search", description: "Search source code, configuration and project files.", status: "Connecting", active: false, tools: [] },
  { id: "analytics", icon: "◒", name: "Analytics", description: "Explore performance and operational signals.", status: "Soon", active: false, tools: [] },
];
const futureAgents = agents.filter((agent) => !agent.server);

function connectedAgents() {
  return agents.filter((agent) => agent.active);
}

function currentAgent() {
  return agents.find((agent) => agent.id === state.view) || agents[0];
}

function agentIcon(id) {
  const icons = {
    github: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" stroke="none" d="M12 2.8a9.2 9.2 0 0 0-2.91 17.93c.46.08.63-.2.63-.44v-1.58c-2.57.56-3.11-1.1-3.11-1.1-.42-1.07-1.03-1.35-1.03-1.35-.84-.57.06-.56.06-.56.93.07 1.42.96 1.42.96.83 1.41 2.18 1 2.71.77.08-.6.33-1 .59-1.23-2.05-.23-4.2-1.03-4.2-4.57 0-1.01.36-1.84.96-2.49-.1-.24-.42-1.18.09-2.45 0 0 .78-.25 2.54.95a8.8 8.8 0 0 1 4.62 0c1.76-1.2 2.54-.95 2.54-.95.51 1.27.19 2.21.09 2.45.6.65.96 1.48.96 2.49 0 3.55-2.16 4.34-4.21 4.57.33.29.63.85.63 1.71v2.53c0 .25.17.53.64.44A9.2 9.2 0 0 0 12 2.8Z"/></svg>',
    playwright: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="2"/></svg>',
    mysql: '<svg viewBox="0 0 24 24" aria-hidden="true"><ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v6c0 1.66 3.13 3 7 3s7-1.34 7-3V6M5 12v6c0 1.66 3.13 3 7 3s7-1.34 7-3v-6"/></svg>',
    "local-search": '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.8" cy="10.8" r="6.2"/><path d="m16 16 4 4"/></svg>',
    analytics: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 19V9m7 10V5m7 14v-7"/><path d="M3 19h18"/></svg>',
    coder: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8.5 7-4 5 4 5M15.5 7l4 5-4 5M13.5 4l-3 16"/></svg>',
    sales: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 17 19 5M10 5h9v9"/><path d="M5 5v14h14"/></svg>',
    marketing: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 1.45 5.55L19 10l-5.55 1.45L12 17l-1.45-5.55L5 10l5.55-1.45L12 3ZM19 16l.6 2.4L22 19l-2.4.6L19 22l-.6-2.4L16 19l2.4-.6L19 16Z"/></svg>'
  };
  return icons[id] || icons.github;
}

function render() {
  if (!state.loggedIn) return renderLanding();
  renderApp();
}

function topbar() {
  const agentTab = !state.loggedIn ? `<button class="header-agent-button ${state.agentMenuOpen ? "open" : ""}" data-action="toggle-agents" aria-expanded="${state.agentMenuOpen}"><span class="summary-icon">${agentIcon("marketing")}</span>Agents <span class="chevron">⌄</span></button>` : "";
  const panel = !state.loggedIn && state.agentMenuOpen ? `<section class="agent-panel"><div class="agent-panel-inner"><div class="agent-panel-heading"><div class="eyebrow">Agent navigation</div><h2>Build your workspace.</h2><p>More focused agents are on the way.</p></div><div class="agent-panel-grid">${futureAgents.map((agent) => `<div class="agent-panel-card"><span class="agent-icon">${agentIcon(agent.id)}</span><span><b>${agent.name}</b><small>In roadmap</small></span></div>`).join("")}</div></div></section>` : "";
  const brandAction = state.loggedIn ? "home" : "landing";
  return `<header class="topbar"><button class="brand" data-action="${brandAction}"><span class="brand-mark">o</span> orbit agents</button><div class="top-actions">${agentTab}<span class="eyebrow">local workspace</span>${state.loggedIn ? `<span class="eyebrow">${escapeHtml(state.username)}</span><button class="button button-danger" data-action="logout">Log out</button>` : `<button class="button button-primary" data-action="login">Enter workspace</button>`}</div></header>${panel}`;
}

function renderLanding() {
  app.innerHTML = `${topbar()}<section class="landing"><div class="hero"><div class="eyebrow">A calmer way to work with AI</div><h1>One workspace.<br /><em>Many agents.</em></h1><p>Connect your local model to the tools that move your work forward. Start with GitHub, then bring analytics, coding, sales and marketing into the same conversational workspace.</p><div class="hero-actions"><button class="button button-primary" data-action="login">Explore agents <span>→</span></button><button class="button button-ghost" data-action="login">See how it works</button></div><div class="hero-note"><span class="dot"></span> Your data stays close to your workflow</div></div><div class="orbit-card"><div class="orbit-core"><div><strong>AI</strong><br /><small>orchestrated</small></div></div><span class="orbit-label label-one">GitHub · active</span><span class="orbit-label label-two">Qwen · local host</span><span class="orbit-label label-three">MCP · connected</span></div></section>`;
}

function renderLogin() {
  app.innerHTML = `${topbar()}<section class="auth-wrap"><form class="auth-card" id="login-form"><button class="back-link" type="button" data-action="landing">← Back to landing</button><div class="eyebrow">Welcome back</div><h1>Enter your workspace.</h1><p>This is a local demo login. Authentication will be connected later.</p><div class="field"><label for="username">Username</label><input id="username" name="username" autocomplete="username" placeholder="samir" required /></div><div class="field"><label for="password">Password</label><input id="password" name="password" type="password" autocomplete="current-password" placeholder="••••••••" required /></div><div class="auth-error" id="auth-error"></div><button class="button button-primary" type="submit" style="width:100%">Continue to agents →</button></form></section>`;
  document.getElementById("login-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    state.username = form.get("username").toString();
    if (!form.get("password").toString()) return;
    state.loggedIn = true;
    sessionStorage.setItem("orbit_logged_in", "true");
    sessionStorage.setItem("orbit_username", state.username);
    state.view = "home";
    render();
    loadCapabilities();
  });
}

function renderApp() {
  const content = state.view === "home" ? dashboard() : agentChat(currentAgent());
  app.innerHTML = `${topbar()}<div class="app-layout"><aside class="sidebar"><div class="eyebrow">Active agents</div><h2>Connected</h2><nav class="agent-nav">${connectedAgents().map(agentButton).join("")}</nav><h2>Workspace</h2><button class="agent-item ${state.view === "home" ? "active" : ""}" data-action="home"><span class="agent-icon">⌂</span><span class="agent-copy"><b>Overview</b><small>All capabilities</small></span></button></aside><section class="workspace">${content}</section></div>`;
  bindAppEvents();
}

function agentButton(agent) {
  return `<button class="agent-item ${state.view === agent.id ? "active" : ""} ${!agent.active ? "disabled-card" : ""}" data-agent="${agent.id}" ${!agent.active ? "disabled" : ""}><span class="agent-icon">${agentIcon(agent.id)}</span><span class="agent-copy"><b>${agent.name}</b><small>${agent.status}</small></span><span class="agent-status ${!agent.active ? "soon" : ""}"></span></button>`;
}

function dashboard() {
  return `<div class="workspace-heading"><div><div class="eyebrow">Good to see you, ${escapeHtml(state.username)}</div><h1>Connected MCP servers.</h1></div><p>The host routes each request to the right MCP server, then asks Qwen to select a tool from that server.</p></div><div class="agent-grid">${connectedAgents().map((agent) => `<button class="agent-card" data-agent="${agent.id}"><div class="card-top"><span class="agent-icon">${agentIcon(agent.id)}</span><span class="tag">${agent.status}</span></div><h3>${agent.name}</h3><p>${agent.description}</p><small class="capability-tools">${agent.tools.length ? `${agent.tools.length} tools connected` : "Tools unavailable"}</small></button>`).join("")}</div>`;
}

function agentChat(agent) {
  const messages = state.messages.length ? state.messages.map(messageMarkup).join("") : `<div class="empty-chat"><strong>Ask ${agent.name} anything.</strong>The host will route your request to the appropriate connected MCP server.</div>`;
  return `<div class="chat-layout"><div class="chat-head"><div><div class="eyebrow">Active agent · ${escapeHtml(agent.name)}</div><h1>${escapeHtml(agent.name)} workspace</h1><p>${escapeHtml(agent.description)}</p></div><button class="button button-ghost" data-action="home">← Agents</button></div><div class="messages" id="messages">${messages}</div><form class="composer" id="chat-form"><textarea id="chat-input" placeholder="Ask about ${escapeHtml(agent.name)}..." ${state.loading ? "disabled" : ""}></textarea><button class="button button-primary" type="submit" ${state.loading ? "disabled" : ""}>${state.loading ? "Working…" : "Send ↑"}</button></form></div>`;
}

function messageMarkup(message) {
  return `<div class="message ${message.role}"><div class="message-meta">${message.role === "user" ? "YOU" : "GITHUB AGENT"}</div><div class="bubble">${escapeHtml(message.text)}${message.tool ? `<br /><span class="tool-pill">tool · ${escapeHtml(message.tool)}</span>` : ""}</div></div>`;
}

function bindAppEvents() {
  document.querySelectorAll("[data-action='login']").forEach((node) => node.addEventListener("click", renderLogin));
  document.querySelectorAll("[data-action='logout']").forEach((node) => node.addEventListener("click", () => { state.loggedIn = false; sessionStorage.clear(); render(); }));
  document.querySelectorAll("[data-action='home']").forEach((node) => node.addEventListener("click", () => { state.view = "home"; render(); }));
  document.querySelectorAll("[data-agent]").forEach((node) => node.addEventListener("click", () => { state.view = node.dataset.agent; render(); }));
  const form = document.getElementById("chat-form");
  if (form) form.addEventListener("submit", sendChat);
}

async function sendChat(event) {
  event.preventDefault();
  const input = document.getElementById("chat-input");
  const message = input.value.trim();
  if (!message || state.loading) return;
  state.messages.push({ role: "user", text: message });
  state.loading = true;
  renderApp();
  try {
    const response = await fetch(`${API_BASE_URL}/chat`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "The agent request failed.");
    const tool = typeof data.tool_name === "string" ? data.tool_name : Array.isArray(data.tool_name) ? data.tool_name.join(", ") : null;
    state.messages.push({ role: "assistant", text: data.answer || "No answer returned.", tool });
  } catch (error) {
    state.messages.push({ role: "assistant", text: `I could not reach the backend. ${error.message}`, tool: null });
  } finally {
    state.loading = false;
    renderApp();
    const messages = document.getElementById("messages");
    if (messages) messages.scrollTop = messages.scrollHeight;
  }
}

function escapeHtml(value) {
  return value.replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character]));
}

document.addEventListener("click", (event) => {
  if (event.target.closest("[data-action='landing']")) {
    state.loggedIn = false;
    state.agentMenuOpen = false;
    sessionStorage.clear();
    renderLanding();
    return;
  }
  if (event.target.closest("[data-action='login']") && !state.loggedIn) renderLogin();
  if (event.target.closest("[data-action='toggle-agents']")) {
    state.agentMenuOpen = !state.agentMenuOpen;
    render();
    return;
  }
  if (state.agentMenuOpen && !event.target.closest(".agent-panel")) {
    state.agentMenuOpen = false;
    render();
  }
});

async function loadCapabilities() {
  try {
    const response = await fetch(`${API_BASE_URL}/mcp/servers`);
    if (!response.ok) return;
    const data = await response.json();
    state.capabilities = data.servers || [];
    agents.forEach((agent) => {
      if (!agent.server) return;
      const capability = state.capabilities.find((item) => item.name === agent.server);
      agent.active = Boolean(capability);
      agent.status = capability ? "Active" : "Unavailable";
      agent.tools = capability ? capability.tools : [];
    });
    if (state.loggedIn) render();
  } catch (_) {
    // Keep the UI usable while the API is starting.
  }
}

render();
loadCapabilities();
