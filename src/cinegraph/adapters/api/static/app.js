"use strict";

const API = Object.freeze({
  health: "/health/ready",
  guest: "/api/v1/auth/guest",
  register: "/api/v1/auth/register",
  login: "/api/v1/auth/login",
  session: "/api/v1/auth/session",
  logout: "/api/v1/auth/logout",
  catalogue: "/api/v1/catalogue",
  chat: "/api/v1/chat",
});

const SPOILER_COPY = Object.freeze({
  relaxed: "Search every episode available to this session.",
  sequential: "Search episodes only through the selected story boundary.",
  strict: "Use only episodes explicitly covered by the selected boundary.",
});

const state = {
  session: null,
  catalogue: null,
  selectedSeriesId: null,
  sending: false,
};

const elements = {
  serviceStatus: document.querySelector("#service-status"),
  serviceStatusText: document.querySelector("#service-status-text"),
  signInButton: document.querySelector("#sign-in-button"),
  accountButton: document.querySelector("#account-button"),
  avatarInitial: document.querySelector("#avatar-initial"),
  welcomeView: document.querySelector("#welcome-view"),
  workspaceView: document.querySelector("#workspace-view"),
  guestStartButton: document.querySelector("#guest-start-button"),
  createAccountButton: document.querySelector("#create-account-button"),
  scopeTitle: document.querySelector("#scope-title"),
  scopeDetail: document.querySelector("#scope-detail"),
  seriesSelect: document.querySelector("#series-select"),
  seasonList: document.querySelector("#season-list"),
  spoilerDescription: document.querySelector("#spoiler-description"),
  boundaryField: document.querySelector("#boundary-field"),
  boundarySelect: document.querySelector("#boundary-select"),
  logoutButton: document.querySelector("#logout-button"),
  mobileScopeButton: document.querySelector("#mobile-scope-button"),
  messages: document.querySelector("#messages"),
  suggestionGrid: document.querySelector("#suggestion-grid"),
  chatForm: document.querySelector("#chat-form"),
  questionInput: document.querySelector("#question-input"),
  sendButton: document.querySelector("#send-button"),
  authDialog: document.querySelector("#auth-dialog"),
  authTitle: document.querySelector("#auth-title"),
  dialogCloseButton: document.querySelector("#dialog-close-button"),
  loginTab: document.querySelector("#login-tab"),
  registerTab: document.querySelector("#register-tab"),
  loginPanel: document.querySelector("#login-panel"),
  registerPanel: document.querySelector("#register-panel"),
  authError: document.querySelector("#auth-error"),
  toast: document.querySelector("#toast"),
};

class ApiError extends Error {
  constructor(response, payload) {
    const detail = payload?.error;
    super(detail?.message || "The request could not be completed.");
    this.status = response.status;
    this.code = detail?.code || "request_failed";
    this.requestId = detail?.request_id || response.headers.get("X-Request-ID");
  }
}

async function apiRequest(path, options = {}) {
  const request = {
    method: options.method || "GET",
    credentials: "same-origin",
    headers: { Accept: "application/json", ...(options.headers || {}) },
  };
  if (options.body !== undefined) {
    request.headers["Content-Type"] = "application/json";
    request.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, request);
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    throw new ApiError(response, payload);
  }
  return payload;
}

function setBusy(button, busy, busyText) {
  if (!button.dataset.originalText) {
    button.dataset.originalText = button.textContent.trim();
  }
  button.disabled = busy;
  button.textContent = busy ? busyText : button.dataset.originalText;
}

let toastTimer;
function showToast(message) {
  window.clearTimeout(toastTimer);
  elements.toast.textContent = message;
  elements.toast.hidden = false;
  toastTimer = window.setTimeout(() => {
    elements.toast.hidden = true;
  }, 4600);
}

function describeError(error) {
  if (error instanceof ApiError) {
    return error.requestId ? `${error.message} Reference: ${error.requestId}` : error.message;
  }
  return "Cinegraph could not be reached. Please try again.";
}

async function updateServiceStatus() {
  try {
    await apiRequest(API.health);
    elements.serviceStatus.classList.add("ready");
    elements.serviceStatus.classList.remove("unavailable");
    elements.serviceStatusText.textContent = "Ready";
  } catch {
    elements.serviceStatus.classList.add("unavailable");
    elements.serviceStatus.classList.remove("ready");
    elements.serviceStatusText.textContent = "Unavailable";
  }
}

function openAuth(mode = "login") {
  setAuthMode(mode);
  elements.authError.hidden = true;
  elements.authDialog.showModal();
}

function setAuthMode(mode) {
  const loginSelected = mode === "login";
  elements.loginTab.setAttribute("aria-selected", String(loginSelected));
  elements.registerTab.setAttribute("aria-selected", String(!loginSelected));
  elements.loginTab.tabIndex = loginSelected ? 0 : -1;
  elements.registerTab.tabIndex = loginSelected ? -1 : 0;
  elements.loginPanel.hidden = !loginSelected;
  elements.registerPanel.hidden = loginSelected;
  elements.authTitle.textContent = loginSelected ? "Continue your story" : "Create your profile";
  elements.authError.hidden = true;
}

function showWelcome() {
  state.session = null;
  state.catalogue = null;
  state.selectedSeriesId = null;
  elements.workspaceView.hidden = true;
  elements.workspaceView.classList.remove("scope-open");
  elements.welcomeView.hidden = false;
  elements.accountButton.hidden = true;
  elements.signInButton.hidden = false;
  window.scrollTo(0, 0);
}

function currentSeries() {
  return state.catalogue?.series.find((item) => item.series_id === state.selectedSeriesId) || null;
}

function allEpisodes(series) {
  if (!series) return [];
  return series.seasons.flatMap((season) =>
    season.episodes.map((episode) => ({
      ...episode,
      season_number: season.season_number,
    })),
  );
}

function renderSeriesControls() {
  const seriesItems = state.catalogue?.series || [];
  elements.seriesSelect.replaceChildren();
  for (const series of seriesItems) {
    const option = document.createElement("option");
    option.value = series.series_id;
    option.textContent = series.series_name;
    elements.seriesSelect.append(option);
  }
  if (!state.selectedSeriesId && seriesItems.length) {
    state.selectedSeriesId = seriesItems[0].series_id;
  }
  elements.seriesSelect.value = state.selectedSeriesId || "";
  renderSeriesScope();
}

function renderSeriesScope() {
  const series = currentSeries();
  elements.seasonList.replaceChildren();
  elements.boundarySelect.replaceChildren();
  if (!series) {
    elements.scopeDetail.textContent = "No corpus is available";
    return;
  }

  for (const season of series.seasons) {
    const chip = document.createElement("span");
    chip.className = "season-chip";
    chip.textContent = `Season ${season.season_number}`;
    elements.seasonList.append(chip);
  }

  for (const episode of allEpisodes(series)) {
    const option = document.createElement("option");
    option.value = episode.episode_id;
    option.textContent = `S${episode.season_number} · E${episode.episode_number} — ${episode.episode_title || "Untitled"}`;
    elements.boundarySelect.append(option);
  }
  const seasons = series.seasons.map((season) => `S${season.season_number}`).join(", ");
  elements.scopeDetail.textContent = `${series.series_name} · ${seasons}`;
}

function updateSessionChrome() {
  const authenticated = state.session?.principal_kind === "authenticated";
  elements.scopeTitle.textContent = authenticated ? "Authenticated library" : "Guest access";
  const initialSource = state.session?.display_name || (authenticated ? "A" : "G");
  elements.avatarInitial.textContent = initialSource.trim().charAt(0).toUpperCase();
  elements.accountButton.hidden = false;
  elements.signInButton.hidden = true;
}

async function enterWorkspace(session) {
  state.session = session;
  state.catalogue = await apiRequest(API.catalogue);
  state.selectedSeriesId = state.catalogue.series[0]?.series_id || null;
  updateSessionChrome();
  renderSeriesControls();
  elements.welcomeView.hidden = true;
  elements.workspaceView.hidden = false;
  window.scrollTo(0, 0);
  elements.questionInput.focus({ preventScroll: true });
}

async function beginGuestSession() {
  setBusy(elements.guestStartButton, true, "Opening the library…");
  try {
    const session = await apiRequest(API.guest, { method: "POST" });
    await enterWorkspace(session);
  } catch (error) {
    showToast(describeError(error));
  } finally {
    setBusy(elements.guestStartButton, false, "");
  }
}

async function restoreSession() {
  try {
    const session = await apiRequest(API.session);
    await enterWorkspace(session);
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 401) {
      showToast(describeError(error));
    }
    showWelcome();
  }
}

function spoilerMode() {
  return document.querySelector('input[name="spoiler-mode"]:checked').value;
}

function updateSpoilerControls() {
  const mode = spoilerMode();
  elements.spoilerDescription.textContent = SPOILER_COPY[mode];
  elements.boundaryField.hidden = mode === "relaxed";
}

function addUserMessage(question) {
  const article = document.createElement("article");
  article.className = "message message-user";
  const content = document.createElement("div");
  content.className = "message-content";
  const text = document.createElement("p");
  text.textContent = question;
  content.append(text);
  article.append(content);
  elements.messages.append(article);
}

function addLoadingMessage() {
  const article = document.createElement("article");
  article.className = "message message-assistant";
  article.dataset.loading = "true";
  const avatar = document.createElement("div");
  avatar.className = "message-avatar";
  avatar.setAttribute("aria-hidden", "true");
  avatar.textContent = "C";
  const content = document.createElement("div");
  content.className = "message-content";
  const label = document.createElement("p");
  label.className = "message-label";
  label.textContent = "Searching the story";
  const dots = document.createElement("div");
  dots.className = "typing-dots";
  dots.setAttribute("aria-label", "Cinegraph is retrieving evidence");
  dots.append(document.createElement("span"), document.createElement("span"), document.createElement("span"));
  content.append(label, dots);
  article.append(avatar, content);
  elements.messages.append(article);
  scrollMessages();
  return article;
}

function formatTimestamp(milliseconds) {
  const totalSeconds = Math.floor(milliseconds / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function citationElement(citation, index) {
  const details = document.createElement("details");
  details.className = "citation-card";
  const summary = document.createElement("summary");
  summary.textContent = `Evidence ${index + 1} · S${citation.season_number} E${citation.episode_number} · ${formatTimestamp(citation.start_ms)}`;
  const quote = document.createElement("blockquote");
  quote.textContent = citation.text;
  details.append(summary, quote);
  return details;
}

function completeAssistantMessage(article, result) {
  article.removeAttribute("data-loading");
  const content = article.querySelector(".message-content");
  content.replaceChildren();
  const label = document.createElement("p");
  label.className = "message-label";
  label.textContent = result.is_safe_refusal ? "Safe refusal" : "Cinegraph";
  const text = document.createElement("p");
  text.textContent = result.answer || "I couldn’t find enough safe, grounded evidence in your current story scope. Try changing the boundary or asking about another moment.";
  content.append(label, text);
  if (result.citations?.length) {
    const citations = document.createElement("div");
    citations.className = "citation-list";
    citations.setAttribute("aria-label", "Transcript evidence");
    result.citations.forEach((citation, index) => citations.append(citationElement(citation, index)));
    content.append(citations);
  }
  scrollMessages();
}

function failAssistantMessage(article, error) {
  article.removeAttribute("data-loading");
  const content = article.querySelector(".message-content");
  content.replaceChildren();
  const label = document.createElement("p");
  label.className = "message-label";
  label.textContent = "Request interrupted";
  const text = document.createElement("p");
  text.textContent = describeError(error);
  content.append(label, text);
  scrollMessages();
}

function scrollMessages() {
  elements.messages.scrollTo({ top: elements.messages.scrollHeight, behavior: "smooth" });
}

async function submitQuestion(question) {
  if (state.sending || !state.selectedSeriesId) return;
  const trimmed = question.trim();
  if (trimmed.length < 2) return;
  state.sending = true;
  elements.sendButton.disabled = true;
  elements.suggestionGrid.hidden = true;
  addUserMessage(trimmed);
  const loading = addLoadingMessage();
  elements.questionInput.value = "";
  resizeQuestionInput();

  const mode = spoilerMode();
  const body = {
    series_id: state.selectedSeriesId,
    question: trimmed,
    spoiler_mode: mode,
  };
  if (mode !== "relaxed") {
    body.safe_through_episode_id = elements.boundarySelect.value;
  }

  try {
    const result = await apiRequest(API.chat, { method: "POST", body });
    completeAssistantMessage(loading, result);
  } catch (error) {
    failAssistantMessage(loading, error);
    if (error instanceof ApiError && error.status === 401) {
      showWelcome();
    }
  } finally {
    state.sending = false;
    elements.sendButton.disabled = false;
    elements.questionInput.focus();
  }
}

function resizeQuestionInput() {
  elements.questionInput.style.height = "auto";
  const targetHeight = Math.min(elements.questionInput.scrollHeight, 150);
  elements.questionInput.style.height = `${targetHeight}px`;
  elements.questionInput.style.overflowY = elements.questionInput.scrollHeight > 150 ? "auto" : "hidden";
}

async function submitAuth(form, path) {
  const button = form.querySelector('button[type="submit"]');
  const data = Object.fromEntries(new FormData(form).entries());
  setBusy(button, true, "Please wait…");
  elements.authError.hidden = true;
  try {
    const session = await apiRequest(path, { method: "POST", body: data });
    elements.authDialog.close();
    form.reset();
    await enterWorkspace(session);
  } catch (error) {
    elements.authError.textContent = describeError(error);
    elements.authError.hidden = false;
  } finally {
    setBusy(button, false, "");
  }
}

async function endSession() {
  setBusy(elements.logoutButton, true, "Ending…");
  try {
    await apiRequest(API.logout, { method: "POST" });
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 401) {
      showToast(describeError(error));
    }
  } finally {
    setBusy(elements.logoutButton, false, "");
    showWelcome();
  }
}

elements.guestStartButton.addEventListener("click", beginGuestSession);
elements.signInButton.addEventListener("click", () => openAuth("login"));
elements.createAccountButton.addEventListener("click", () => openAuth("register"));
elements.dialogCloseButton.addEventListener("click", () => elements.authDialog.close());
elements.loginTab.addEventListener("click", () => setAuthMode("login"));
elements.registerTab.addEventListener("click", () => setAuthMode("register"));
elements.loginPanel.addEventListener("submit", (event) => {
  event.preventDefault();
  submitAuth(elements.loginPanel, API.login);
});
elements.registerPanel.addEventListener("submit", (event) => {
  event.preventDefault();
  submitAuth(elements.registerPanel, API.register);
});
elements.authDialog.addEventListener("click", (event) => {
  if (event.target === elements.authDialog) elements.authDialog.close();
});
elements.seriesSelect.addEventListener("change", () => {
  state.selectedSeriesId = elements.seriesSelect.value;
  renderSeriesScope();
});
document.querySelectorAll('input[name="spoiler-mode"]').forEach((input) => {
  input.addEventListener("change", updateSpoilerControls);
});
elements.chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  submitQuestion(elements.questionInput.value);
});
elements.questionInput.addEventListener("input", resizeQuestionInput);
elements.questionInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    elements.chatForm.requestSubmit();
  }
});
elements.suggestionGrid.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (button) submitQuestion(button.textContent);
});
elements.logoutButton.addEventListener("click", endSession);
elements.accountButton.addEventListener("click", () => {
  elements.workspaceView.classList.toggle("scope-open");
  elements.mobileScopeButton.setAttribute(
    "aria-expanded",
    String(elements.workspaceView.classList.contains("scope-open")),
  );
});
elements.mobileScopeButton.addEventListener("click", () => {
  const opened = elements.workspaceView.classList.toggle("scope-open");
  elements.mobileScopeButton.setAttribute("aria-expanded", String(opened));
});

updateSpoilerControls();
resizeQuestionInput();
updateServiceStatus();
restoreSession();
