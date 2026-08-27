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

async function apiRequest(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const request = {
    method,
    credentials: "same-origin",
    headers: { Accept: "application/json", ...(options.headers || {}) },
  };
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
  state.session = null;
  state.catalogue = null;
  state.selectedSeriesId = null;
  state.selectedLibrarySeason = null;
  state.selectedLibraryEpisodeId = null;
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

async function enterWorkspace(session) {
  state.session = session;
  state.catalogue = await apiRequest(API.catalogue);
  state.selectedSeriesId = state.catalogue.series[0]?.series_id || null;
  state.selectedLibrarySeason = null;
  state.selectedLibraryEpisodeId = null;
  updateSessionChrome();
  renderSeriesControls();
  elements.welcomeView.hidden = true;
  elements.workspaceView.hidden = false;
  setScopeOpen(false, { restoreFocus: false });
  window.scrollTo(0, 0);
  elements.questionInput.focus({ preventScroll: true });
}

async function beginGuestSession() {
  setBusy(elements.guestStartButton, true, UI_COPY.busy.guest);
  try {
    const session = await apiRequest(API.guest, { method: "POST" });
    await enterWorkspace(session);
  } catch (error) {
    showToast(describeError(error));
  } finally {
  setBusy(elements.guestStartButton, false, UI_COPY.busy.guest);
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
  label.textContent = result.is_safe_refusal
    ? UI_COPY.assistant.safeRefusal
    : UI_COPY.assistant.name;
  const text = document.createElement("p");
  text.textContent = result.answer || UI_COPY.assistant.fallback;
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
  label.textContent = UI_COPY.assistant.interrupted;
  const text = document.createElement("p");
  text.textContent = describeError(error);
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

async function submitQuestion(question) {
  if (state.sending || !state.selectedSeriesId) return;
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
    elements.sendButton.setAttribute("aria-busy", "false");
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
  setBusy(button, true, UI_COPY.busy.auth);
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
    setBusy(button, false, UI_COPY.busy.auth);
  }
}

async function endSession() {
  setBusy(elements.logoutButton, true, UI_COPY.busy.logout);
  try {
    await apiRequest(API.logout, { method: "POST" });
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 401) {
      showToast(describeError(error));
    }
  } finally {
    setBusy(elements.logoutButton, false, UI_COPY.busy.logout);
    showWelcome();
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
