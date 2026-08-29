"use strict";

const API = Object.freeze({
  health: "/health/ready",
  guest: "/api/v1/auth/guest",
  register: "/api/v1/auth/register",
  login: "/api/v1/auth/login",
  session: "/api/v1/auth/session",
  logout: "/api/v1/auth/logout",
  catalogue: "/api/v1/catalogue",
  agentJobs: "/api/v1/agent/jobs",
});

const SPOILER_COPY = Object.freeze({
  relaxed: "Search every episode available to this session.",
  sequential: "Search episodes only through the selected story boundary.",
  strict: "Use only episodes explicitly covered by the selected boundary.",
});

const UI_COPY = Object.freeze({
  authTitle: Object.freeze({
    login: "Continue your story",
    register: "Create your profile",
  }),
  busy: Object.freeze({
    guest: "Opening the library…",
    auth: "Please wait…",
    logout: "Ending…",
  }),
  assistant: Object.freeze({
    loadingLabel: "Searching the story",
    loadingDescription: "Cinegraph is retrieving evidence",
    name: "Cinegraph",
    safeRefusal: "Safe refusal",
    fallback: "I couldn’t find enough safe, grounded evidence in your current story scope. Try changing the boundary or asking about another moment.",
    interrupted: "Request interrupted",
    queued: "Queued for research",
    running: "Researching the story",
    reconnecting: "Reconnecting to research",
    scopeChanged: "Research stopped because the story scope changed.",
    jobFailed: "Cinegraph could not complete this research request.",
    jobErrors: Object.freeze({
      execution_timeout: "The research request took too long. Try a narrower question.",
      provider_unavailable: "The research service is temporarily unavailable. Please try again.",
      budget_exceeded: "This research request reached its configured limit. Try a narrower question.",
      agent_execution_failed: "Cinegraph could not complete this research request.",
      agent_dispatch_unavailable: "Research is temporarily busy. Please try again shortly.",
    }),
    unknownTime: "Unknown time",
  }),
  evidence: Object.freeze({
    title: "Evidence trail",
    defaultTool: "Grounded research",
    inspect: "Inspect supporting moment",
    hide: "Hide supporting moment",
    transcript: "Transcript evidence",
    graph: "Story relationship",
    supportingMoments: (count) => `${count} supporting ${count === 1 ? "moment" : "moments"}`,
    noEvidence: "No authorized evidence was returned for this answer.",
    hydrationUnavailable: "Supporting excerpts are unavailable for this answer. The locator is retained, but the source text could not be loaded.",
    hydrationFailed: "This answer is withheld because authorized evidence could not be loaded for the current scope.",
    legacyGraph: "This relationship came from an older response format and cannot be expanded safely.",
    polarity: "Polarity",
    hops: (count) => `${count} ${count === 1 ? "hop" : "hops"}`,
    score: (value) => `Support score ${value}`,
    tool: "Research route",
    explore: "Explore story connections",
    exploreQuestion: (subject, object) => `How are ${subject} and ${object} connected?`,
    emptyGraph: "No authorized story relationships were found in this scope.",
    unknownEntity: "Unknown entity",
    unknownKind: "kind unavailable",
    unknownRelationship: "has a relationship with",
    unspecifiedPolarity: "unspecified",
    distanceUnavailable: "Distance unavailable",
    subjectLabel: "Subject",
    objectLabel: "Object",
    predicateLabel: "Relationship",
  }),
  tools: Object.freeze({
    grounded_transcript_answer: "Transcript research",
    authorized_graph_relationships: "Relationship research",
  }),
  scope: Object.freeze({
    noCorpus: "No corpus is available",
    guestTitle: "Guest access",
    authenticatedTitle: "Authenticated library",
  }),
  library: Object.freeze({
    noSeries: "Series unavailable",
    noSeasons: "No seasons are available to this session.",
    noEpisodes: "No episodes are available in this season.",
    noEpisode: "Choose an episode",
    noEpisodeDescription: "Episode details will appear here when you choose an episode.",
    noPoster: "Poster unavailable",
    noMetadata: "Reviewed cast metadata is not available for this series yet.",
    unknownEpisode: "Untitled episode",
    unknownPosition: "?",
    unknownCharacter: "Character not listed",
    sourceLabel: "Source",
    sourceLinkLabel: "View provider details",
    episodeCount: (count) => `${count} ${count === 1 ? "episode" : "episodes"}`,
  }),
  errors: Object.freeze({
    requestFailed: "The request could not be completed.",
    unreachable: "Cinegraph could not be reached. Please try again.",
  }),
  service: Object.freeze({ ready: "Ready", unavailable: "Unavailable" }),
});

const AGENT_RUNTIME = Object.freeze({
  pollIntervalMs: 1200,
  maximumPollAttempts: 75,
  maximumJobDurationMs: 90_000,
  maximumExcerptLength: 4_000,
  maximumRenderedCitations: 32,
});

const AUTH_MODES = Object.freeze(["login", "register"]);
const SCOPE_DRAWER_QUERY = "(max-width: 900px)";
const SCOPE_DRAWER_MEDIA = window.matchMedia(SCOPE_DRAWER_QUERY);
const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled]):not([tabindex='-1'])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

const state = {
  session: null,
  catalogue: null,
  selectedSeriesId: null,
  selectedLibrarySeason: null,
  selectedLibraryEpisodeId: null,
  sending: false,
  threadId: null,
  scopeRevision: 0,
  activeJob: null,
  authEpoch: 0,
  authController: null,
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
  libraryOpenButton: document.querySelector("#library-open-button"),
  libraryDialog: document.querySelector("#library-dialog"),
  libraryCloseButton: document.querySelector("#library-close-button"),
  libraryPoster: document.querySelector("#library-poster"),
  libraryPosterFallback: document.querySelector("#library-poster-fallback"),
  librarySeriesTitle: document.querySelector("#library-series-title"),
  libraryScope: document.querySelector("#library-scope"),
  libraryAttribution: document.querySelector("#library-attribution"),
  librarySeasonList: document.querySelector("#library-season-list"),
  libraryEpisodeList: document.querySelector("#library-episode-list"),
  libraryEpisodeCount: document.querySelector("#library-episode-count"),
  libraryDetailPosition: document.querySelector("#library-detail-position"),
  libraryDetailTitle: document.querySelector("#library-detail-title"),
  libraryDetailEmpty: document.querySelector("#library-detail-empty"),
  libraryRegularCastSection: document.querySelector("#library-regular-cast-section"),
  libraryRegularCast: document.querySelector("#library-regular-cast"),
  libraryGuestCastSection: document.querySelector("#library-guest-cast-section"),
  libraryGuestCast: document.querySelector("#library-guest-cast"),
  libraryGuestCastEmpty: document.querySelector("#library-guest-cast-empty"),
  libraryCastEmpty: document.querySelector("#library-cast-empty"),
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
  skipLink: document.querySelector(".skip-link"),
  topbar: document.querySelector(".topbar"),
  corpusPanel: document.querySelector(".corpus-panel"),
  conversationPanel: document.querySelector(".conversation-panel"),
  scopeCloseButton: document.querySelector("#scope-close-button"),
  scopeBackdrop: document.querySelector(".scope-backdrop"),
};

let authReturnFocus = null;
let scopeReturnFocus = null;
let libraryReturnFocus = null;
let evidenceTrailSequence = 0;

class ApiError extends Error {
  constructor(response, payload) {
    const detail = payload?.error;
    super(detail?.message || UI_COPY.errors.requestFailed);
    this.status = response.status;
    this.code = detail?.code || "request_failed";
    this.requestId = detail?.request_id || response.headers.get("X-Request-ID");
  }
}

function readCookie(name) {
  const encoded = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(`${name}=`));
  return encoded ? decodeURIComponent(encoded.slice(name.length + 1)) : null;
}

function csrfToken() {
  // Prefer the production __Host- cookie if a browser retained both names
  // during an environment transition.
  return readCookie("__Host-cinegraph_csrf") || readCookie("cinegraph_csrf");
}

function beginAuthIntent() {
  state.authController?.abort();
  const controller = new AbortController();
  state.authEpoch += 1;
  state.authController = controller;
  return Object.freeze({ epoch: state.authEpoch, controller });
}

function invalidateAuthIntent() {
  state.authEpoch += 1;
  state.authController?.abort();
  state.authController = null;
}

function isCurrentAuthIntent(intent) {
  return Boolean(
    intent
      && state.authEpoch === intent.epoch
      && state.authController === intent.controller
      && !intent.controller.signal.aborted,
  );
}

async function apiRequest(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const request = {
    method,
    credentials: "same-origin",
    headers: { Accept: "application/json", ...(options.headers || {}) },
  };
  if (options.signal) request.signal = options.signal;
  if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
    const csrf = csrfToken();
    if (csrf) request.headers["X-CSRF-Token"] = csrf;
  }
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
  if (!button) return;
  if (!button.dataset.originalAriaCaptured) {
    button.dataset.originalAriaCaptured = "true";
    button.dataset.originalAriaLabel = button.getAttribute("aria-label") || "";
    button.dataset.hadAriaLabel = button.hasAttribute("aria-label") ? "true" : "false";
  }
  const labelNode = [...button.childNodes].find(
    (node) => node.nodeType === Node.TEXT_NODE && node.textContent.trim(),
  );
  if (labelNode && !button.dataset.originalText) {
    button.dataset.originalText = labelNode.textContent;
  }
  const originalText = button.dataset.originalText || "";
  if (labelNode) labelNode.textContent = busy ? ` ${busyText} ` : originalText;
  button.disabled = busy;
  button.setAttribute("aria-busy", String(busy));
  button.classList.toggle("is-busy", busy);
  if (busy) {
    button.setAttribute("aria-label", busyText);
  } else if (button.dataset.hadAriaLabel === "true") {
    button.setAttribute("aria-label", button.dataset.originalAriaLabel);
  } else {
    button.removeAttribute("aria-label");
  }
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
  return UI_COPY.errors.unreachable;
}

async function updateServiceStatus() {
  try {
    await apiRequest(API.health);
    elements.serviceStatus.classList.add("ready");
    elements.serviceStatus.classList.remove("unavailable");
    elements.serviceStatusText.textContent = UI_COPY.service.ready;
  } catch {
    elements.serviceStatus.classList.add("unavailable");
    elements.serviceStatus.classList.remove("ready");
    elements.serviceStatusText.textContent = UI_COPY.service.unavailable;
  }
}

function openAuth(mode = "login") {
  authReturnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  setAuthMode(mode);
  elements.authError.hidden = true;
  elements.authDialog.showModal();
  focusAuthPanel(mode);
}

function setAuthMode(mode) {
  const selectedMode = AUTH_MODES.includes(mode) ? mode : "login";
  const loginSelected = selectedMode === "login";
  elements.loginTab.setAttribute("aria-selected", String(loginSelected));
  elements.registerTab.setAttribute("aria-selected", String(!loginSelected));
  elements.loginTab.tabIndex = loginSelected ? 0 : -1;
  elements.registerTab.tabIndex = loginSelected ? -1 : 0;
  elements.loginPanel.hidden = !loginSelected;
  elements.registerPanel.hidden = loginSelected;
  elements.authTitle.textContent = UI_COPY.authTitle[selectedMode];
  elements.authError.hidden = true;
}

function authTabs() {
  return [elements.loginTab, elements.registerTab];
}

function authModeForTab(tab) {
  return tab === elements.registerTab ? "register" : "login";
}

function focusAuthPanel(mode) {
  const panel = mode === "register" ? elements.registerPanel : elements.loginPanel;
  const firstControl = panel.querySelector("input:not([disabled]), button:not([disabled])");
  if (firstControl) firstControl.focus({ preventScroll: true });
}

function handleAuthTabKeydown(event) {
  const tabs = authTabs();
  const currentIndex = tabs.indexOf(event.currentTarget);
  if (currentIndex < 0) return;
  let nextIndex = currentIndex;
  if (event.key === "ArrowRight" || event.key === "ArrowDown") nextIndex = (currentIndex + 1) % tabs.length;
  if (event.key === "ArrowLeft" || event.key === "ArrowUp") nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
  if (event.key === "Home") nextIndex = 0;
  if (event.key === "End") nextIndex = tabs.length - 1;
  if (nextIndex === currentIndex) return;
  event.preventDefault();
  const nextTab = tabs[nextIndex];
  nextTab.focus();
  setAuthMode(authModeForTab(nextTab));
}

function closeAuth() {
  if (elements.authDialog.open) elements.authDialog.close();
}

function setElementIsolation(element, isolated) {
  if (!element) return;
  element.inert = isolated;
  element.toggleAttribute("inert", isolated);
  if (isolated) element.setAttribute("aria-hidden", "true");
  else element.removeAttribute("aria-hidden");
}

function scopeFocusableElements() {
  if (!elements.corpusPanel) return [];
  return [...elements.corpusPanel.querySelectorAll(FOCUSABLE_SELECTOR)].filter(
    (element) => !element.closest("[hidden]") && element.getClientRects().length > 0,
  );
}

function setScopeOpen(open, { restoreFocus = true } = {}) {
  if (!elements.workspaceView || !elements.mobileScopeButton) return;
  const drawerMode = SCOPE_DRAWER_MEDIA.matches;
  const isOpen = Boolean(open && drawerMode);
  elements.workspaceView.classList.toggle("scope-open", isOpen);
  elements.mobileScopeButton.setAttribute("aria-expanded", String(isOpen));
  elements.accountButton.setAttribute("aria-expanded", String(isOpen));
  document.body.classList.toggle("scope-scroll-locked", isOpen);
  if (elements.scopeBackdrop) {
    elements.scopeBackdrop.hidden = !isOpen;
  }
  setElementIsolation(elements.skipLink, isOpen);
  setElementIsolation(elements.topbar, isOpen);
  setElementIsolation(elements.conversationPanel, isOpen);
  if (elements.corpusPanel) {
    const closedDrawer = drawerMode && !isOpen;
    elements.corpusPanel.inert = closedDrawer;
    elements.corpusPanel.toggleAttribute("inert", closedDrawer);
    if (drawerMode) {
      elements.corpusPanel.setAttribute("role", "dialog");
      elements.corpusPanel.setAttribute("aria-hidden", String(!isOpen));
      if (isOpen) elements.corpusPanel.setAttribute("aria-modal", "true");
      else elements.corpusPanel.removeAttribute("aria-modal");
    } else {
      elements.corpusPanel.removeAttribute("role");
      elements.corpusPanel.removeAttribute("aria-hidden");
      elements.corpusPanel.removeAttribute("aria-modal");
    }
  }
  if (isOpen) {
    const [firstControl] = scopeFocusableElements();
    if (firstControl) firstControl.focus({ preventScroll: true });
  } else if (restoreFocus && scopeReturnFocus instanceof HTMLElement) {
    scopeReturnFocus.focus({ preventScroll: true });
    scopeReturnFocus = null;
  } else if (!isOpen) {
    scopeReturnFocus = null;
  }
}

function showWelcome() {
  invalidateAuthIntent();
  stopActiveAgentJob(UI_COPY.assistant.interrupted);
  state.session = null;
  state.catalogue = null;
  state.selectedSeriesId = null;
  state.selectedLibrarySeason = null;
  state.selectedLibraryEpisodeId = null;
  state.threadId = null;
  state.scopeRevision += 1;
  closeLibrary();
  elements.workspaceView.hidden = true;
  setScopeOpen(false, { restoreFocus: false });
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

function formatSeasonScope(seasons) {
  const numbers = seasons.map((season) => season.season_number);
  if (!numbers.length) return UI_COPY.scope.noCorpus;
  if (numbers.length === 1) return `Season ${numbers[0]}`;
  const contiguous = numbers.every(
    (seasonNumber, index) => index === 0 || seasonNumber === numbers[index - 1] + 1,
  );
  return contiguous
    ? `Seasons ${numbers[0]}–${numbers[numbers.length - 1]}`
    : `Seasons ${numbers.join(", ")}`;
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
    elements.scopeDetail.textContent = UI_COPY.scope.noCorpus;
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
  elements.scopeDetail.textContent = `${series.series_name} · ${formatSeasonScope(series.seasons)}`;
}

function safeSameOriginMediaUrl(value) {
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    const url = new URL(value, window.location.origin);
    return url.origin === window.location.origin ? url.href : null;
  } catch {
    return null;
  }
}

function safeCanonicalUrl(value) {
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

function appendExternalLink(parent, label, value) {
  const href = safeCanonicalUrl(value);
  if (!href) return false;
  const link = document.createElement("a");
  link.href = href;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = label;
  parent.append(link);
  return true;
}

function renderLibraryPoster(series) {
  const poster = series?.poster;
  const mediaUrl = safeSameOriginMediaUrl(poster?.url);
  elements.libraryPoster.hidden = true;
  elements.libraryPoster.removeAttribute("src");
  elements.libraryPoster.removeAttribute("width");
  elements.libraryPoster.removeAttribute("height");
  elements.libraryPoster.alt = poster?.alt || (series ? `${series.series_name} poster` : "");
  elements.libraryPosterFallback.textContent = mediaUrl ? "Loading poster…" : UI_COPY.library.noPoster;
  elements.libraryPosterFallback.hidden = false;
  elements.libraryPoster.onerror = () => {
    elements.libraryPoster.hidden = true;
    elements.libraryPosterFallback.textContent = UI_COPY.library.noPoster;
    elements.libraryPosterFallback.hidden = false;
  };
  if (!mediaUrl) return;
  const width = Number.isInteger(poster?.width) && poster.width > 0 ? poster.width : null;
  const height = Number.isInteger(poster?.height) && poster.height > 0 ? poster.height : null;
  if (width) elements.libraryPoster.width = width;
  if (height) elements.libraryPoster.height = height;
  elements.libraryPoster.src = mediaUrl;
  elements.libraryPoster.hidden = false;
  elements.libraryPosterFallback.hidden = true;
}

function renderLibraryAttribution(series) {
  elements.libraryAttribution.replaceChildren();
  const source = series?.metadata_source;
  if (!source?.provider_name && !source?.canonical_url && !source?.attribution) {
    elements.libraryAttribution.hidden = true;
    return;
  }
  elements.libraryAttribution.hidden = false;
  const label = document.createElement("span");
  label.textContent = `${UI_COPY.library.sourceLabel}: ${source.provider_name || "Metadata provider"}`;
  elements.libraryAttribution.append(label);
  if (source.attribution) {
    const attribution = document.createElement("span");
    attribution.textContent = ` · ${source.attribution}`;
    elements.libraryAttribution.append(attribution);
  }
  if (source.license_name) {
    const license = document.createElement("span");
    license.textContent = ` · ${source.license_name}`;
    elements.libraryAttribution.append(license);
  }
  if (source.canonical_url) {
    elements.libraryAttribution.append(document.createTextNode(" · "));
    appendExternalLink(elements.libraryAttribution, UI_COPY.library.sourceLinkLabel, source.canonical_url);
  }
}

function creditItem(credit) {
  if (!credit || typeof credit.name !== "string" || !credit.name.trim()) return null;
  const item = document.createElement("li");
  const name = document.createElement("span");
  if (!appendExternalLink(name, credit.name, credit.canonical_url)) name.textContent = credit.name;
  item.append(name);
  const character = document.createElement("span");
  character.className = "cast-character";
  if (typeof credit.character_name === "string" && credit.character_name.trim()) {
    if (!appendExternalLink(character, credit.character_name, credit.character_canonical_url)) {
      character.textContent = credit.character_name;
    }
  } else {
    character.textContent = UI_COPY.library.unknownCharacter;
  }
  item.append(character);
  return item;
}

function renderCreditList(list, credits) {
  list.replaceChildren();
  for (const credit of Array.isArray(credits) ? credits : []) {
    const item = creditItem(credit);
    if (item) list.append(item);
  }
  return list.childElementCount > 0;
}

function librarySeasons(series) {
  return Array.isArray(series?.seasons) ? series.seasons : [];
}

function selectedLibrarySeason(series) {
  const seasons = librarySeasons(series);
  return seasons.find((season) => season.season_number === state.selectedLibrarySeason) || seasons[0] || null;
}

function libraryFocusableElements() {
  if (!elements.libraryDialog) return [];
  return [...elements.libraryDialog.querySelectorAll(FOCUSABLE_SELECTOR)].filter(
    (element) => !element.closest("[hidden]") && element.getClientRects().length > 0,
  );
}

function selectLibraryEpisode(episodeId) {
  state.selectedLibraryEpisodeId = episodeId || null;
  renderLibraryEpisodeList(selectedLibrarySeason(currentSeries()));
  renderLibraryDetail();
  elements.libraryEpisodeList
    .querySelector('.library-episode-button[aria-pressed="true"]')
    ?.focus({ preventScroll: true });
}

function renderLibrarySeasonList(series) {
  elements.librarySeasonList.replaceChildren();
  for (const season of librarySeasons(series)) {
    const button = document.createElement("button");
    button.className = "library-season-button";
    button.type = "button";
    const label = document.createElement("span");
    label.textContent = `Season ${season.season_number}`;
    button.append(label);
    button.setAttribute("aria-pressed", String(season.season_number === state.selectedLibrarySeason));
    button.addEventListener("click", () => {
      state.selectedLibrarySeason = season.season_number;
      const firstEpisode = season.episodes?.[0];
      state.selectedLibraryEpisodeId = firstEpisode?.episode_id || null;
      renderLibrarySeasonList(series);
      renderLibraryEpisodeList(season);
      renderLibraryDetail();
      elements.librarySeasonList
        .querySelector('.library-season-button[aria-pressed="true"]')
        ?.focus({ preventScroll: true });
    });
    elements.librarySeasonList.append(button);
  }
}

function renderLibraryEpisodeList(season) {
  elements.libraryEpisodeList.replaceChildren();
  const episodes = Array.isArray(season?.episodes) ? season.episodes : [];
  elements.libraryEpisodeCount.textContent = episodes.length ? UI_COPY.library.episodeCount(episodes.length) : "";
  if (!episodes.length) {
    const empty = document.createElement("p");
    empty.className = "library-episode-empty";
    empty.textContent = UI_COPY.library.noEpisodes;
    elements.libraryEpisodeList.append(empty);
    return;
  }
  for (const episode of episodes) {
    const button = document.createElement("button");
    button.className = "library-episode-button";
    button.type = "button";
    button.setAttribute("aria-pressed", String(episode.episode_id === state.selectedLibraryEpisodeId));
    const position = document.createElement("span");
    position.className = "episode-position";
    position.textContent = `S${season.season_number} · E${episode.episode_number}`;
    const title = document.createElement("span");
    title.className = "episode-title";
    title.textContent = episode.episode_title || UI_COPY.library.unknownEpisode;
    button.append(position, title);
    button.addEventListener("click", () => selectLibraryEpisode(episode.episode_id));
    const item = document.createElement("div");
    item.setAttribute("role", "listitem");
    item.append(button);
    elements.libraryEpisodeList.append(item);
  }
}

function renderLibraryDetail() {
  const series = currentSeries();
  const season = selectedLibrarySeason(series);
  const episode = season?.episodes?.find((item) => item.episode_id === state.selectedLibraryEpisodeId) || null;
  elements.libraryRegularCastSection.hidden = true;
  elements.libraryGuestCastSection.hidden = true;
  elements.libraryGuestCastEmpty.hidden = true;
  elements.libraryCastEmpty.hidden = true;
  elements.libraryRegularCast.replaceChildren();
  elements.libraryGuestCast.replaceChildren();
  if (!episode) {
    elements.libraryDetailPosition.textContent = "No episode selected";
    elements.libraryDetailTitle.textContent = UI_COPY.library.noEpisode;
    elements.libraryDetailEmpty.textContent = UI_COPY.library.noEpisodeDescription;
    elements.libraryDetailEmpty.hidden = false;
    elements.libraryCastEmpty.hidden = true;
    return;
  }
  elements.libraryDetailEmpty.hidden = true;
  elements.libraryDetailPosition.textContent = `Season ${season.season_number} · Episode ${episode.episode_number}`;
  elements.libraryDetailTitle.textContent = episode.episode_title || UI_COPY.library.unknownEpisode;
  const regulars = renderCreditList(elements.libraryRegularCast, series?.regular_cast);
  const guests = renderCreditList(elements.libraryGuestCast, episode.guest_cast);
  const metadataAvailable = Boolean(series?.metadata_source);
  elements.libraryRegularCastSection.hidden = !regulars;
  elements.libraryGuestCastSection.hidden = !metadataAvailable && !guests;
  elements.libraryGuestCastEmpty.hidden = guests;
  elements.libraryCastEmpty.textContent = UI_COPY.library.noMetadata;
  elements.libraryCastEmpty.hidden = metadataAvailable || regulars || guests;
}

function renderLibrary() {
  const series = currentSeries();
  const seasons = librarySeasons(series);
  if (!series) {
    elements.librarySeriesTitle.textContent = UI_COPY.library.noSeries;
    elements.libraryScope.textContent = UI_COPY.library.noSeasons;
    elements.librarySeasonList.replaceChildren();
    elements.libraryEpisodeList.replaceChildren();
    elements.libraryEpisodeCount.textContent = "";
    renderLibraryPoster(null);
    renderLibraryAttribution(null);
    state.selectedLibrarySeason = null;
    state.selectedLibraryEpisodeId = null;
    renderLibraryDetail();
    return;
  }
  const season = selectedLibrarySeason(series);
  state.selectedLibrarySeason = season?.season_number ?? null;
  const episode = season?.episodes?.find((item) => item.episode_id === state.selectedLibraryEpisodeId) || season?.episodes?.[0] || null;
  state.selectedLibraryEpisodeId = episode?.episode_id || null;
  elements.librarySeriesTitle.textContent = series.series_name;
  elements.libraryScope.textContent = `${formatSeasonScope(seasons)} available to this session.`;
  renderLibraryPoster(series);
  renderLibraryAttribution(series);
  renderLibrarySeasonList(series);
  renderLibraryEpisodeList(season);
  renderLibraryDetail();
}

function openLibrary() {
  if (!elements.libraryDialog || elements.libraryDialog.open) return;
  libraryReturnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  renderLibrary();
  elements.libraryDialog.showModal();
  const [firstControl] = libraryFocusableElements();
  if (firstControl) firstControl.focus({ preventScroll: true });
}

function closeLibrary() {
  if (elements.libraryDialog?.open) elements.libraryDialog.close();
}

function updateSessionChrome() {
  const authenticated = state.session?.principal_kind === "authenticated";
  elements.scopeTitle.textContent = authenticated
    ? UI_COPY.scope.authenticatedTitle
    : UI_COPY.scope.guestTitle;
  const initialSource = state.session?.display_name || (authenticated ? "A" : "G");
  elements.avatarInitial.textContent = initialSource.trim().charAt(0).toUpperCase();
  elements.accountButton.hidden = false;
  elements.signInButton.hidden = true;
}

async function enterWorkspace(session, intent = null) {
  stopActiveAgentJob(UI_COPY.assistant.interrupted);
  if (intent && !isCurrentAuthIntent(intent)) return false;
  const catalogue = await apiRequest(API.catalogue, intent ? { signal: intent.controller.signal } : {});
  if (intent && !isCurrentAuthIntent(intent)) return false;
  state.session = session;
  state.catalogue = catalogue;
  state.selectedSeriesId = state.catalogue.series[0]?.series_id || null;
  state.selectedLibrarySeason = null;
  state.selectedLibraryEpisodeId = null;
  state.scopeRevision += 1;
  state.threadId = createInMemoryId();
  updateSessionChrome();
  renderSeriesControls();
  elements.welcomeView.hidden = true;
  elements.workspaceView.hidden = false;
  setScopeOpen(false, { restoreFocus: false });
  window.scrollTo(0, 0);
  elements.questionInput.focus({ preventScroll: true });
  return true;
}

async function beginGuestSession() {
  const intent = beginAuthIntent();
  setBusy(elements.guestStartButton, true, UI_COPY.busy.guest);
  try {
    const session = await apiRequest(API.guest, { method: "POST", signal: intent.controller.signal });
    if (!isCurrentAuthIntent(intent)) return;
    await enterWorkspace(session, intent);
  } catch (error) {
    if (error?.name !== "AbortError" && isCurrentAuthIntent(intent)) showToast(describeError(error));
  } finally {
    if (isCurrentAuthIntent(intent)) {
      setBusy(elements.guestStartButton, false, UI_COPY.busy.guest);
      state.authController = null;
    }
  }
}

async function restoreSession() {
  const intent = beginAuthIntent();
  try {
    const session = await apiRequest(API.session, { signal: intent.controller.signal });
    if (!isCurrentAuthIntent(intent)) return;
    await enterWorkspace(session, intent);
  } catch (error) {
    if (error?.name === "AbortError" || !isCurrentAuthIntent(intent)) return;
    if (!(error instanceof ApiError) || error.status !== 401) {
      showToast(describeError(error));
    }
    showWelcome();
  } finally {
    if (isCurrentAuthIntent(intent)) state.authController = null;
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
  label.textContent = UI_COPY.assistant.loadingLabel;
  const dots = document.createElement("div");
  dots.className = "typing-dots";
  dots.setAttribute("role", "status");
  dots.setAttribute("aria-label", UI_COPY.assistant.loadingDescription);
  for (let index = 0; index < 3; index += 1) {
    const dot = document.createElement("span");
    dot.setAttribute("aria-hidden", "true");
    dots.append(dot);
  }
  content.append(label, dots);
  article.append(avatar, content);
  elements.messages.append(article);
  scrollMessages();
  return article;
}

function formatTimestamp(milliseconds) {
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return UI_COPY.assistant.unknownTime;
  const totalSeconds = Math.floor(milliseconds / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function safeDomId(prefix, value) {
  const candidate = typeof value === "string" ? value.replace(/[^a-zA-Z0-9_-]/g, "-") : "item";
  return `${prefix}-${candidate || "item"}`;
}

function humanToolLabel(name) {
  return UI_COPY.tools[name] || UI_COPY.evidence.defaultTool;
}

function citationIdentity(citation, index) {
  if (typeof citation?.citation_id === "string" && citation.citation_id) return citation.citation_id;
  if (typeof citation?.segment_id === "string" && citation.segment_id) return citation.segment_id;
  if (typeof citation?.evidence_id === "string" && citation.evidence_id) return citation.evidence_id;
  return `citation-${index}`;
}

function citationEpisodeLabel(citation) {
  const season = Number.isInteger(citation?.season_number) ? citation.season_number : UI_COPY.library.unknownPosition;
  const episode = Number.isInteger(citation?.episode_number) ? citation.episode_number : UI_COPY.library.unknownPosition;
  return `S${season} E${episode}`;
}

function formatGraphTerm(value) {
  if (typeof value !== "string" || !value.trim()) return UI_COPY.evidence.unknownKind;
  return value.trim().replace(/[_-]+/g, " ").replace(/\b\w/g, (character) => character.toUpperCase());
}

function createDisclosure(summaryText, detailText, id) {
  const details = document.createElement("details");
  details.className = "evidence-disclosure";
  details.id = id;
  const summary = document.createElement("summary");
  summary.textContent = summaryText;
  const detail = document.createElement("div");
  detail.className = "evidence-disclosure-body";
  detail.textContent = detailText;
  details.append(summary, detail);
  return details;
}

function transcriptEvidenceElement(citation, index, toolLabel) {
  const card = document.createElement("article");
  card.className = "evidence-card evidence-transcript";
  const header = document.createElement("header");
  const kind = document.createElement("span");
  kind.className = "evidence-kind";
  kind.textContent = UI_COPY.evidence.transcript;
  const meta = document.createElement("span");
  meta.className = "evidence-meta";
  meta.textContent = `${citationEpisodeLabel(citation)} · ${formatTimestamp(citation.start_ms)}`;
  header.append(kind, meta);
  const excerpt = typeof citation.excerpt === "string" && citation.excerpt.trim()
    ? citation.excerpt
    : typeof citation.text === "string" && citation.text.trim()
      ? citation.text
      : null;
  const quote = document.createElement("blockquote");
  quote.textContent = excerpt || UI_COPY.evidence.hydrationUnavailable;
  const source = document.createElement("p");
  source.className = "evidence-source";
  source.textContent = `${toolLabel} · ${UI_COPY.evidence.inspect}`;
  card.append(header, quote, source);
  return card;
}

function graphEvidenceElement(citation, index, toolLabel, trailId) {
  const graph = citation?.graph;
  if (!graph || typeof graph !== "object") {
    const fallback = document.createElement("article");
    fallback.className = "evidence-card evidence-legacy";
    fallback.textContent = UI_COPY.evidence.legacyGraph;
    return fallback;
  }
  const card = document.createElement("article");
  card.className = "evidence-card evidence-relationship";
  const heading = document.createElement("h4");
  const subject = document.createElement("span");
  subject.className = "relationship-entity";
  subject.textContent = graph.subject?.display_name || UI_COPY.evidence.unknownEntity;
  const predicate = document.createElement("span");
  predicate.className = "relationship-predicate";
  predicate.textContent = graph.predicate ? formatGraphTerm(graph.predicate) : UI_COPY.evidence.unknownRelationship;
  const object = document.createElement("span");
  object.className = "relationship-entity";
  object.textContent = graph.object?.display_name || UI_COPY.evidence.unknownEntity;
  const subjectKind = document.createElement("span");
  subjectKind.className = "relationship-kind";
  subjectKind.textContent = `(${formatGraphTerm(graph.subject?.kind)})`;
  const objectKind = document.createElement("span");
  objectKind.className = "relationship-kind";
  objectKind.textContent = `(${formatGraphTerm(graph.object?.kind)})`;
  heading.append(
    document.createTextNode(`${UI_COPY.evidence.subjectLabel}: `),
    subject,
    subjectKind,
    document.createTextNode(` · ${UI_COPY.evidence.predicateLabel}: `),
    predicate,
    document.createTextNode(` · ${UI_COPY.evidence.objectLabel}: `),
    object,
    objectKind,
  );
  const facts = document.createElement("p");
  facts.className = "relationship-facts";
  const polarity = graph.polarity || UI_COPY.evidence.unspecifiedPolarity;
  const hops = Number.isInteger(graph.hop_distance) ? UI_COPY.evidence.hops(graph.hop_distance) : UI_COPY.evidence.distanceUnavailable;
  const support = Number.isInteger(citation.supporting_moment_count)
    ? Math.max(0, Math.min(citation.supporting_moment_count, AGENT_RUNTIME.maximumRenderedCitations))
    : Math.min(Array.isArray(citation.supporting_moments) ? citation.supporting_moments.length : 1, AGENT_RUNTIME.maximumRenderedCitations);
  const score = typeof graph.score === "number" && Number.isFinite(graph.score) && graph.score >= 0 && graph.score <= 1
    ? ` · ${UI_COPY.evidence.score(graph.score.toFixed(2))}`
    : "";
  facts.textContent = `${UI_COPY.evidence.polarity}: ${polarity} · ${hops}${score} · ${UI_COPY.evidence.supportingMoments(support)}`;
  const source = document.createElement("p");
  source.className = "evidence-source";
  source.textContent = `${toolLabel} · ${citationEpisodeLabel(citation)} · ${formatTimestamp(citation.start_ms)}`;
  card.append(heading, facts, source);
  const explore = document.createElement("button");
  explore.type = "button";
  explore.className = "evidence-explore-button";
  explore.textContent = UI_COPY.evidence.explore;
  explore.addEventListener("click", () => {
    const subjectName = graph.subject?.display_name || UI_COPY.evidence.unknownEntity;
    const objectName = graph.object?.display_name || UI_COPY.evidence.unknownEntity;
    elements.questionInput.value = UI_COPY.evidence.exploreQuestion(subjectName, objectName);
    resizeQuestionInput();
    elements.questionInput.focus({ preventScroll: true });
  });
  card.append(explore);
  const moments = (Array.isArray(citation.supporting_moments) ? citation.supporting_moments : [citation])
    .slice(0, AGENT_RUNTIME.maximumRenderedCitations);
  const list = document.createElement("div");
  list.className = "relationship-moments";
  moments.forEach((moment, momentIndex) => {
    const excerpt = typeof moment.excerpt === "string" && moment.excerpt.trim()
      ? moment.excerpt
      : typeof moment.text === "string" && moment.text.trim()
        ? moment.text
        : UI_COPY.evidence.hydrationUnavailable;
    const detail = createDisclosure(
      `${UI_COPY.evidence.supportingMoments(1)} · ${citationEpisodeLabel(moment)} · ${formatTimestamp(moment.start_ms)}`,
      excerpt,
      safeDomId("supporting-moment", `${trailId}-${citationIdentity(citation, index)}-${momentIndex}`),
    );
    list.append(detail);
  });
  card.append(list);
  return card;
}

function evidenceTrailElement(result) {
  const section = document.createElement("section");
  section.className = "evidence-trail";
  const trailId = `evidence-trail-${++evidenceTrailSequence}`;
  section.setAttribute("aria-labelledby", `${trailId}-title`);
  const heading = document.createElement("h3");
  heading.id = `${trailId}-title`;
  heading.textContent = UI_COPY.evidence.title;
  section.append(heading);
  const tools = Array.isArray(result?.used_tools) ? result.used_tools.map(humanToolLabel) : [];
  if (tools.length) {
    const route = document.createElement("p");
    route.className = "evidence-route";
    route.textContent = `${UI_COPY.evidence.tool}: ${tools.join(" · ")}`;
    section.append(route);
  }
  if (result?.is_safe_refusal) {
    const empty = document.createElement("p");
    empty.className = "evidence-empty";
    empty.textContent = UI_COPY.evidence.noEvidence;
    section.append(empty);
    return section;
  }
  const citations = Array.isArray(result?.citations)
    ? result.citations.slice(0, AGENT_RUNTIME.maximumRenderedCitations)
    : [];
  if (!citations.length) {
    const empty = document.createElement("p");
    empty.className = "evidence-empty";
    empty.textContent = UI_COPY.evidence.noEvidence;
    section.append(empty);
    return section;
  }
  const groupedCitations = [];
  const graphClaims = new Map();
  citations.forEach((citation) => {
    if (citation.kind !== "graph" || !citation.graph) {
      groupedCitations.push(citation);
      return;
    }
    const graph = citation.graph;
    const key = [
      citation.claim_id || graph.claim_id || "",
      graph.subject?.entity_id || "",
      graph.subject?.kind || "",
      graph.predicate || "",
      graph.object?.entity_id || "",
      graph.object?.kind || "",
      graph.polarity || UI_COPY.evidence.unspecifiedPolarity,
    ].join("|");
    const prior = graphClaims.get(key);
    const moments = Array.isArray(citation.supporting_moments) && citation.supporting_moments.length
      ? citation.supporting_moments
      : [citation];
    if (!prior) {
      const grouped = { ...citation, supporting_moments: [] };
      const seenMomentIds = new Set();
      moments.forEach((moment, momentIndex) => {
        const id = citationIdentity(moment, momentIndex);
        if (!seenMomentIds.has(id)) {
          seenMomentIds.add(id);
          grouped.supporting_moments.push(moment);
        }
      });
      graphClaims.set(key, { citation: grouped, seenMomentIds });
      groupedCitations.push(grouped);
    } else {
      moments.forEach((moment, momentIndex) => {
        const id = citationIdentity(moment, momentIndex);
        if (!prior.seenMomentIds.has(id)) {
          prior.seenMomentIds.add(id);
          prior.citation.supporting_moments.push(moment);
        }
      });
    }
  });
  const list = document.createElement("ol");
  list.className = "evidence-list";
  groupedCitations.forEach((citation, index) => {
    const item = document.createElement("li");
    item.className = "evidence-list-item";
    const toolLabel = humanToolLabel(citation.tool_name || citation.tool || "");
    const isGraph = citation.kind === "graph";
    const card = isGraph
      ? graphEvidenceElement(citation, index, toolLabel, trailId)
      : transcriptEvidenceElement(citation, index, toolLabel);
    item.append(card);
    list.append(item);
  });
  section.append(list);
  return section;
}

function completeAssistantMessage(article, result) {
  article.removeAttribute("data-loading");
  const content = article.querySelector(".message-content");
  content.replaceChildren();
  const label = document.createElement("p");
  label.className = "message-label";
  label.textContent = result.is_safe_refusal
    ? UI_COPY.assistant.safeRefusal
    : UI_COPY.assistant.name;
  const text = document.createElement("p");
  text.textContent = result.answer || UI_COPY.assistant.fallback;
  content.append(label, text);
  content.append(evidenceTrailElement(result));
  scrollMessages();
}

function failAssistantMessage(article, error, message = null) {
  article.removeAttribute("data-loading");
  const content = article.querySelector(".message-content");
  content.replaceChildren();
  const label = document.createElement("p");
  label.className = "message-label";
  label.textContent = UI_COPY.assistant.interrupted;
  const text = document.createElement("p");
  text.textContent = message || describeError(error);
  content.append(label, text);
  scrollMessages();
}

function scrollMessages() {
  const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  elements.messages.scrollTo({
    top: elements.messages.scrollHeight,
    behavior: reducedMotion ? "auto" : "smooth",
  });
}

function createInMemoryId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  throw new Error(UI_COPY.assistant.jobFailed);
}

function canonicalAgentJobId(value) {
  // Job IDs are deterministic UUIDv5 values; thread and idempotency IDs are
  // UUIDv4. Accept every canonical lowercase UUID version at this boundary.
  if (typeof value !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value)) return null;
  return value;
}

function strictAgentUrl(value, jobId, suffix) {
  const canonicalId = canonicalAgentJobId(jobId);
  if (typeof value !== "string" || !value.trim() || !canonicalId) return null;
  try {
    const url = new URL(value, window.location.origin);
    const expectedPath = `/api/v1/agent/jobs/${canonicalId}${suffix}`;
    if (
      url.origin !== window.location.origin
      || url.username
      || url.password
      || url.search
      || url.hash
      || url.pathname !== expectedPath
    ) return null;
    return url.href;
  } catch {
    return null;
  }
}

function isCurrentAgentJob(job) {
  return Boolean(
    job
      && state.activeJob === job
      && job.scopeRevision === state.scopeRevision
      && job.threadId === state.threadId
      && job.seriesId === state.selectedSeriesId,
  );
}

function setJobStatus(article, labelText, description) {
  const label = article?.querySelector(".message-label");
  const status = article?.querySelector(".typing-dots");
  if (label) label.textContent = labelText;
  if (status && description) status.setAttribute("aria-label", description);
}

function closeJobTransport(job, { clearDeadline = true } = {}) {
  if (!job) return;
  if (job.eventSource) {
    job.eventSource.close();
    job.eventSource = null;
  }
  if (job.pollTimer !== null) {
    window.clearTimeout(job.pollTimer);
    job.pollTimer = null;
  }
  if (clearDeadline && job.deadlineTimer !== null) {
    window.clearTimeout(job.deadlineTimer);
    job.deadlineTimer = null;
  }
}

function stopActiveAgentJob(message = UI_COPY.assistant.scopeChanged) {
  const job = state.activeJob;
  if (!job) return;
  closeJobTransport(job);
  job.controller?.abort();
  state.activeJob = null;
  state.sending = false;
  elements.sendButton.disabled = false;
  elements.sendButton.setAttribute("aria-busy", "false");
  if (job.article?.isConnected) {
    failAssistantMessage(job.article, new Error(message), message);
  }
}

function rotateAgentThread() {
  stopActiveAgentJob();
  state.scopeRevision += 1;
  state.threadId = createInMemoryId();
}

function terminalJobStatus(status) {
  return ["succeeded", "safe_refusal", "failed"].includes(status);
}

function hydrateAgentResult(result, payload, jobId) {
  if (!result || typeof result !== "object" || !payload || typeof payload !== "object") {
    throw new Error(UI_COPY.evidence.hydrationFailed);
  }
  if (payload.job_id !== jobId || !Array.isArray(payload.items)) {
    throw new Error(UI_COPY.evidence.hydrationFailed);
  }
  const citations = Array.isArray(result.citations) ? result.citations : [];
  if ((!result.is_safe_refusal && !citations.length) || (result.is_safe_refusal && citations.length)) {
    throw new Error(UI_COPY.evidence.hydrationFailed);
  }
  const citationIds = citations.map((citation) => citation?.citation_id);
  if (citationIds.some((id) => typeof id !== "string" || !id) || new Set(citationIds).size !== citationIds.length) {
    throw new Error(UI_COPY.evidence.hydrationFailed);
  }
  const hydrated = new Map();
  for (const item of payload.items) {
    if (
      !item
      || typeof item !== "object"
      || typeof item.citation_id !== "string"
      || !item.citation_id
      || typeof item.excerpt !== "string"
      || !item.excerpt.trim()
      || item.excerpt.length > AGENT_RUNTIME.maximumExcerptLength
      || hydrated.has(item.citation_id)
    ) throw new Error(UI_COPY.evidence.hydrationFailed);
    hydrated.set(item.citation_id, item.excerpt);
  }
  if (hydrated.size !== citationIds.length || citationIds.some((id) => !hydrated.has(id))) {
    throw new Error(UI_COPY.evidence.hydrationFailed);
  }
  return {
    ...result,
    citations: citations.map((citation, index) => {
      const id = citationIds[index];
      const moments = Array.isArray(citation.supporting_moments)
        ? citation.supporting_moments.map((moment, momentIndex) => {
          const momentId = moment?.citation_id || citationIdentity(moment, momentIndex);
          return hydrated.has(momentId) ? { ...moment, excerpt: hydrated.get(momentId) } : moment;
        })
        : citation.supporting_moments;
      return { ...citation, excerpt: hydrated.get(id), ...(moments ? { supporting_moments: moments } : {}) };
    }),
  };
}

function parseJobEvent(event) {
  if (!event?.data) return {};
  try {
    const payload = JSON.parse(event.data);
    return payload && typeof payload === "object" ? payload : {};
  } catch {
    return {};
  }
}

function jobEventStatus(event) {
  const payload = parseJobEvent(event);
  return payload.status || event?.type || "";
}

function jobFailureMessage(code) {
  return UI_COPY.assistant.jobErrors[code] || UI_COPY.assistant.jobFailed;
}

async function finishAgentJob(job, statusPayload = null) {
  if (!isCurrentAgentJob(job) || job.finishing) return;
  job.finishing = true;
  // Keep the overall deadline alive while the terminal status and one batch
  // evidence hydration request complete.
  closeJobTransport(job, { clearDeadline: false });
  try {
    const status = statusPayload || await apiRequest(job.statusUrl, { signal: job.controller.signal });
    if (!isCurrentAgentJob(job)) return;
    if (status?.status === "failed") {
      throw Object.assign(new Error(jobFailureMessage(status.error_code)), {
        userMessage: jobFailureMessage(status.error_code),
      });
    }
    const result = status?.result;
    if (!result || typeof result !== "object") {
      throw new Error(UI_COPY.assistant.jobFailed);
    }
    let hydrated = result;
    const evidenceUrl = strictAgentUrl(result.evidence_url, job.jobId, "/evidence");
    if ((!result.is_safe_refusal && !evidenceUrl) || (result.evidence_url && !evidenceUrl)) {
      throw Object.assign(new Error(UI_COPY.evidence.hydrationFailed), {
        userMessage: UI_COPY.evidence.hydrationFailed,
      });
    }
    if (evidenceUrl) {
      try {
        const evidence = await apiRequest(evidenceUrl, { signal: job.controller.signal });
        hydrated = hydrateAgentResult(result, evidence, job.jobId);
        if (!isCurrentAgentJob(job)) return;
      } catch (error) {
        if (error?.name === "AbortError" || !isCurrentAgentJob(job)) return;
        failAgentJob(job, error, UI_COPY.evidence.hydrationFailed);
        return;
      }
    }
    if (!isCurrentAgentJob(job)) return;
    completeAssistantMessage(job.article, hydrated);
    finalizeAgentJob(job);
  } catch (error) {
    if (error?.name === "AbortError" || !isCurrentAgentJob(job)) return;
    failAgentJob(job, error, error instanceof ApiError ? null : (error.userMessage || UI_COPY.assistant.jobFailed));
  }
}

function finalizeAgentJob(job) {
  if (!isCurrentAgentJob(job)) return;
  closeJobTransport(job);
  state.activeJob = null;
  state.sending = false;
  elements.sendButton.disabled = false;
  elements.sendButton.setAttribute("aria-busy", "false");
  elements.questionInput.focus({ preventScroll: true });
}

function failAgentJob(job, error, message = null) {
  if (!isCurrentAgentJob(job)) return;
  job.controller?.abort();
  failAssistantMessage(job.article, error, message);
  finalizeAgentJob(job);
}

async function pollAgentJob(job) {
  if (!isCurrentAgentJob(job) || job.finishing) return;
  job.pollAttempts += 1;
  if (job.pollAttempts > AGENT_RUNTIME.maximumPollAttempts) {
    failAgentJob(job, new Error(UI_COPY.assistant.jobFailed), UI_COPY.assistant.jobFailed);
    return;
  }
  try {
    const status = await apiRequest(job.statusUrl, { signal: job.controller.signal });
    if (!isCurrentAgentJob(job)) return;
    if (terminalJobStatus(status?.status)) {
      await finishAgentJob(job, status);
      return;
    }
    setJobStatus(job.article, status?.status === "queued" ? UI_COPY.assistant.queued : UI_COPY.assistant.running, UI_COPY.assistant.loadingDescription);
    job.pollTimer = window.setTimeout(() => pollAgentJob(job), AGENT_RUNTIME.pollIntervalMs);
  } catch (error) {
    if (error?.name === "AbortError" || !isCurrentAgentJob(job)) return;
    if (job.pollAttempts >= AGENT_RUNTIME.maximumPollAttempts) failAgentJob(job, error, UI_COPY.assistant.jobFailed);
    else job.pollTimer = window.setTimeout(() => pollAgentJob(job), AGENT_RUNTIME.pollIntervalMs);
  }
}

function startAgentEvents(job) {
  const eventsUrl = strictAgentUrl(job.eventsUrl, job.jobId, "/events");
  if (typeof EventSource === "undefined" || !eventsUrl) {
    pollAgentJob(job);
    return;
  }
  try {
    const source = new EventSource(eventsUrl, { withCredentials: true });
    job.eventSource = source;
    source.onopen = () => {
      if (isCurrentAgentJob(job)) setJobStatus(job.article, UI_COPY.assistant.running, UI_COPY.assistant.loadingDescription);
    };
    const handleEvent = (event) => {
      if (!isCurrentAgentJob(job)) return;
      const status = jobEventStatus(event);
      if (status === "queued") setJobStatus(job.article, UI_COPY.assistant.queued, UI_COPY.assistant.loadingDescription);
      else if (status === "running") setJobStatus(job.article, UI_COPY.assistant.running, UI_COPY.assistant.loadingDescription);
      if (terminalJobStatus(status)) finishAgentJob(job);
    };
    ["queued", "running", "succeeded", "safe_refusal", "failed", "message"].forEach((name) => source.addEventListener(name, handleEvent));
    source.onerror = () => {
      if (!isCurrentAgentJob(job) || job.finishing) return;
      setJobStatus(job.article, UI_COPY.assistant.reconnecting, UI_COPY.assistant.loadingDescription);
      // Keep EventSource alive so the browser can reconnect with its
      // Last-Event-ID cursor; polling runs in parallel as a bounded fallback.
      if (job.pollTimer === null) job.pollTimer = window.setTimeout(() => pollAgentJob(job), AGENT_RUNTIME.pollIntervalMs);
    };
  } catch {
    pollAgentJob(job);
  }
}

async function submitQuestion(question) {
  if (state.sending || !state.selectedSeriesId || !state.session) return;
  const trimmed = question.trim();
  if (trimmed.length < 2) return;
  state.sending = true;
  elements.sendButton.disabled = true;
  elements.sendButton.setAttribute("aria-busy", "true");
  elements.suggestionGrid.hidden = true;
  addUserMessage(trimmed);
  const loading = addLoadingMessage();
  elements.questionInput.value = "";
  resizeQuestionInput();

  const mode = spoilerMode();
  const job = {
    article: loading,
    controller: new AbortController(),
    scopeRevision: state.scopeRevision,
    threadId: state.threadId || (state.threadId = createInMemoryId()),
    seriesId: state.selectedSeriesId,
    pollAttempts: 0,
    pollTimer: null,
    eventSource: null,
    finishing: false,
    deadlineTimer: null,
    statusUrl: null,
    eventsUrl: null,
    jobId: null,
  };
  state.activeJob = job;
  job.deadlineTimer = window.setTimeout(() => {
    if (isCurrentAgentJob(job)) failAgentJob(job, new Error(UI_COPY.assistant.jobFailed), UI_COPY.assistant.jobFailed);
  }, AGENT_RUNTIME.maximumJobDurationMs);
  try {
    const body = {
      thread_id: job.threadId,
      series_id: job.seriesId,
      question: trimmed,
      spoiler_mode: mode,
    };
    if (mode !== "relaxed") body.safe_through_episode_id = elements.boundarySelect.value;
    const response = await apiRequest(API.agentJobs, {
      method: "POST",
      headers: { "Idempotency-Key": createInMemoryId() },
      body,
      signal: job.controller.signal,
    });
    if (!isCurrentAgentJob(job)) return;
    job.jobId = canonicalAgentJobId(response?.job_id);
    job.statusUrl = strictAgentUrl(response?.status_url, job.jobId, "");
    job.eventsUrl = strictAgentUrl(response?.events_url, job.jobId, "/events");
    if (!job.jobId || !job.statusUrl || !job.eventsUrl) throw new Error(UI_COPY.assistant.jobFailed);
    startAgentEvents(job);
  } catch (error) {
    if (error?.name !== "AbortError" && isCurrentAgentJob(job)) {
      failAgentJob(job, error);
      if (error instanceof ApiError && error.status === 401) showWelcome();
    }
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
  const intent = beginAuthIntent();
  setBusy(button, true, UI_COPY.busy.auth);
  elements.authError.hidden = true;
  try {
    const session = await apiRequest(path, { method: "POST", body: data, signal: intent.controller.signal });
    if (!isCurrentAuthIntent(intent)) return;
    elements.authDialog.close();
    form.reset();
    await enterWorkspace(session, intent);
  } catch (error) {
    if (error?.name !== "AbortError" && isCurrentAuthIntent(intent)) {
      elements.authError.textContent = describeError(error);
      elements.authError.hidden = false;
    }
  } finally {
    if (isCurrentAuthIntent(intent)) {
      setBusy(button, false, UI_COPY.busy.auth);
      state.authController = null;
    }
  }
}

async function endSession() {
  const intent = beginAuthIntent();
  setBusy(elements.logoutButton, true, UI_COPY.busy.logout);
  try {
    await apiRequest(API.logout, { method: "POST", signal: intent.controller.signal });
    if (!isCurrentAuthIntent(intent)) return;
  } catch (error) {
    if (error?.name !== "AbortError" && isCurrentAuthIntent(intent) && (!(error instanceof ApiError) || error.status !== 401)) {
      showToast(describeError(error));
    }
  } finally {
    if (isCurrentAuthIntent(intent)) {
      setBusy(elements.logoutButton, false, UI_COPY.busy.logout);
      state.authController = null;
      showWelcome();
    }
  }
}

elements.guestStartButton.addEventListener("click", beginGuestSession);
elements.signInButton.addEventListener("click", () => openAuth("login"));
elements.createAccountButton.addEventListener("click", () => openAuth("register"));
elements.dialogCloseButton.addEventListener("click", closeAuth);
elements.loginTab.addEventListener("click", () => {
  setAuthMode("login");
  focusAuthPanel("login");
});
elements.registerTab.addEventListener("click", () => {
  setAuthMode("register");
  focusAuthPanel("register");
});
elements.loginTab.addEventListener("keydown", handleAuthTabKeydown);
elements.registerTab.addEventListener("keydown", handleAuthTabKeydown);
elements.loginPanel.addEventListener("submit", (event) => {
  event.preventDefault();
  submitAuth(elements.loginPanel, API.login);
});
elements.registerPanel.addEventListener("submit", (event) => {
  event.preventDefault();
  submitAuth(elements.registerPanel, API.register);
});
elements.authDialog.addEventListener("click", (event) => {
  if (event.target === elements.authDialog) closeAuth();
});
elements.authDialog.addEventListener("close", () => {
  if (authReturnFocus instanceof HTMLElement) authReturnFocus.focus({ preventScroll: true });
  authReturnFocus = null;
});
elements.seriesSelect.addEventListener("change", () => {
  rotateAgentThread();
  state.selectedSeriesId = elements.seriesSelect.value;
  state.selectedLibrarySeason = null;
  state.selectedLibraryEpisodeId = null;
  renderSeriesScope();
  if (elements.libraryDialog?.open) renderLibrary();
  if (elements.workspaceView.classList.contains("scope-open")) setScopeOpen(false);
});
elements.libraryOpenButton.addEventListener("click", openLibrary);
elements.libraryCloseButton.addEventListener("click", closeLibrary);
elements.libraryDialog.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeLibrary();
});
elements.libraryDialog.addEventListener("click", (event) => {
  if (event.target === elements.libraryDialog) closeLibrary();
});
elements.libraryDialog.addEventListener("close", () => {
  if (libraryReturnFocus instanceof HTMLElement) libraryReturnFocus.focus({ preventScroll: true });
  libraryReturnFocus = null;
});
document.querySelectorAll('input[name="spoiler-mode"]').forEach((input) => {
  input.addEventListener("change", () => {
    rotateAgentThread();
    updateSpoilerControls();
  });
});
elements.boundarySelect.addEventListener("change", rotateAgentThread);
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
  if (!SCOPE_DRAWER_MEDIA.matches) {
    const [firstScopeControl] = scopeFocusableElements();
    if (firstScopeControl) firstScopeControl.focus({ preventScroll: true });
    return;
  }
  const opened = elements.workspaceView.classList.contains("scope-open");
  if (!opened) scopeReturnFocus = elements.accountButton;
  setScopeOpen(!opened);
});
elements.mobileScopeButton.addEventListener("click", () => {
  const opened = elements.workspaceView.classList.contains("scope-open");
  if (!opened) scopeReturnFocus = elements.mobileScopeButton;
  setScopeOpen(!opened);
});
if (elements.scopeBackdrop) {
  elements.scopeBackdrop.addEventListener("click", (event) => {
    if (event.target === event.currentTarget) setScopeOpen(false);
  });
}
elements.scopeCloseButton.addEventListener("click", () => setScopeOpen(false));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && elements.authDialog.open) {
    event.preventDefault();
    closeAuth();
    return;
  }
  if (event.key === "Escape" && elements.workspaceView.classList.contains("scope-open")) {
    event.preventDefault();
    setScopeOpen(false);
    return;
  }
  if (event.key === "Tab" && elements.workspaceView.classList.contains("scope-open")) {
    const focusable = scopeFocusableElements();
    if (!focusable.length) return;
    const firstControl = focusable[0];
    const lastControl = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === firstControl) {
      event.preventDefault();
      lastControl.focus();
    } else if (!event.shiftKey && document.activeElement === lastControl) {
      event.preventDefault();
      firstControl.focus();
    }
  }
  if (event.key === "Tab" && elements.libraryDialog?.open) {
    const focusable = libraryFocusableElements();
    if (!focusable.length) return;
    const firstControl = focusable[0];
    const lastControl = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === firstControl) {
      event.preventDefault();
      lastControl.focus();
    } else if (!event.shiftKey && document.activeElement === lastControl) {
      event.preventDefault();
      firstControl.focus();
    }
  }
});
SCOPE_DRAWER_MEDIA.addEventListener("change", (event) => {
  if (!event.matches) setScopeOpen(false, { restoreFocus: false });
});

updateSpoilerControls();
resizeQuestionInput();
updateServiceStatus();
restoreSession();
