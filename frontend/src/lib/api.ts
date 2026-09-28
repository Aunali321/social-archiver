/** Typed client for the archiver's API. Same origin in production; vite proxies /api in dev. */

export interface MediaRef {
	index: number;
	type: string | null;
	available: boolean;
}

export interface Item {
	item_id: string;
	platform: string;
	category: string;
	author_username: string;
	author_id: string | null;
	post_url: string;
	text: string | null;
	title: string | null;
	is_article: boolean;
	created_at: string | null;
	chat_name: string | null;
	subreddit: string | null;
	link_url: string | null;
	collection_name: string | null;
	shared_by_username: string | null;
	product_type: string | null;
	conversation_id: string | null;
	in_reply_to_status_id: string | null;
	quoted_tweet_id: string | null;
	retweeted_tweet_id: string | null;
	is_retweet: boolean;
	origin: string | null;
	discovered_via_item_id: string | null;
	thread_root_id: string | null;
	source_target: string | null;
	reply_count: number | null;
	retweet_count: number | null;
	like_count: number | null;
	quote_count: number | null;
	bookmark_count: number | null;
	view_count: number | null;
	archive_status: string;
	upload_status: string;
	embed_status: string;
	archive_error: string | null;
	vlm_description: string | null;
	telegram_message_ids: number[];
	media: MediaRef[];
	context: ItemContext | null;
	in_thread: boolean;
	is_seed: boolean;
}

export interface ConversationNode {
	item: Item;
	replies: ConversationNode[];
}

export interface Conversation {
	focus: Item;
	ancestors: Item[];
	missing_parent: boolean;
	replies: ConversationNode[];
}

export interface ItemContext {
	parent: Item | null;
	quoted: Item | null;
	retweeted: Item | null;
	submission: Item | null;
}

export interface Page {
	items: Item[];
	next_cursor: string | null;
}

export interface SearchHit {
	item: Item;
	snippet: string | null; // matched post text; matches between \u0002 and \u0003
	media_snippet: string | null; // matched media caption, same marks
	score: number | null;
}

export interface SearchResult {
	mode: string;
	hits: SearchHit[];
	semantic_platforms: string[];
}

export interface ItemDetail {
	item: Item;
	categories: string[];
	collections: string[];
	parent: Item | null;
	quoted: Item | null;
	retweeted: Item | null;
	discovered_via: Item | null;
	replies: Item[];
}

export interface Chat {
	chat_id: string;
	name: string | null;
	category: string;
	message_count: number;
	last_at: string | null;
	last_author: string | null;
	last_text: string | null;
}

export interface Facets {
	categories: Record<string, number>;
	origins: Record<string, number>;
	subreddits: Record<string, number>;
	collections: Record<string, number>;
}

export interface ArchiveStats {
	platform: string;
	total: number;
	categories: Record<string, number>;
	archive: Record<string, number>;
	upload: Record<string, number>;
	embed: Record<string, number>;
	with_media: number;
	with_local_media: number;
	authors: number;
	oldest: string | null;
	newest: string | null;
	by_month: Record<string, number>;
}

/* Control plane (admin) */
export interface PlatformStatus {
	platform: string;
	categories: string[];
	total: number;
	archive: Record<string, number>;
	upload: Record<string, number>;
	caption: Record<string, number>;
	embed: Record<string, number>;
	resumable: string[];
	scheduled: boolean;
	scheduled_categories: string[];
	next_run: string | null;
	interval_minutes: number | null;
	sidecar: string | null;
	error: string | null;
}

export interface Job {
	id: number;
	platform: string;
	job: string;
	flags: string;
	status: string;
	source: string;
	queued_at: string | null;
	finished_at: string | null;
	error: string | null;
}

export interface ControlStatus {
	platforms: PlatformStatus[];
	active: Job[];
	recent: Job[];
}

export interface TrackedSource {
	platform: string;
	kind: string;
	target: string;
	enabled: boolean;
	last_run: string | null;
	items: number;
}

export interface SourcesResult {
	kinds: Record<string, string[]>;
	sources: TrackedSource[];
}

export class ApiError extends Error {
	constructor(
		public status: number,
		message: string
	) {
		super(message);
	}
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
	const response = await fetch(path, init);
	const body = await response.json().catch(() => null);
	// A lapsed session sends the page to sign in and back; auth calls report their own 401s
	if (response.status === 401 && !path.startsWith('/api/auth/')) {
		const here = location.pathname + location.search;
		location.assign(`/login${query({ next: here })}`);
	}
	if (!response.ok) {
		throw new ApiError(response.status, body?.detail ?? response.statusText);
	}
	// Control-plane endpoints report failures as {"error": "..."} with HTTP 200
	if (body && typeof body === 'object' && 'error' in body && body.error) {
		throw new ApiError(response.status, body.error as string);
	}
	return body as T;
}

function query(params: Record<string, string | number | boolean | null | undefined>): string {
	const search = new URLSearchParams();
	for (const [key, value] of Object.entries(params)) {
		if (value !== undefined && value !== null && value !== '') search.set(key, String(value));
	}
	const text = search.toString();
	return text ? `?${text}` : '';
}

export type SearchSort = 'relevance' | 'newest' | 'oldest';
export type MatchField = 'text' | 'media' | 'names';
export type MediaFilter = 'image' | 'video' | 'gif' | 'audio' | 'sticker' | 'document';
export type ItemKind = 'post' | 'reply' | 'repost' | 'quote';
export type PostFormat = 'reel' | 'post' | 'carousel';

export interface ItemFilters {
	platforms?: string;
	seeds_only?: boolean;
	category?: string;
	author?: string;
	subreddit?: string;
	chat?: string;
	collection?: string;
	origin?: string;
	archive_status?: string;
	has_media?: boolean;
	media?: MediaFilter;
	kind?: ItemKind;
	has_link?: boolean;
	post_format?: PostFormat;
	date_from?: string;
	date_to?: string;
}

function post<T>(path: string, body: unknown = {}): Promise<T> {
	return request<T>(path, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(body)
	});
}

export interface Authorization {
	client_name: string;
	redirect_uri: string;
}

export const api = {
	/* Sign-in */
	session: () => request<{ authenticated: boolean }>('/api/auth/session'),
	login: (password: string) => post<{ authenticated: boolean }>('/api/auth/login', { password }),
	logout: () => post<{ authenticated: boolean }>('/api/auth/logout'),
	authorization: (id: string) =>
		request<Authorization>(`/api/auth/authorize/${encodeURIComponent(id)}`),
	decide: (id: string, approve: boolean) =>
		post<{ redirect: string }>(`/api/auth/authorize/${encodeURIComponent(id)}`, { approve }),

	platforms: () => request<{ platforms: string[]; semantic: string[] }>('/api/platforms'),
	items: (filters: ItemFilters, cursor?: string | null, limit = 40) =>
		request<Page>(`/api/items${query({ ...filters, cursor, limit })}`),
	item: (platform: string, id: string) =>
		request<ItemDetail>(`/api/items/${platform}/${encodeURIComponent(id)}`),
	conversation: (platform: string, id: string) =>
		request<Conversation>(`/api/conversation/${platform}/${encodeURIComponent(id)}`),
	thread: (platform: string, rootId: string) =>
		request<Item[]>(`/api/threads/${platform}/${encodeURIComponent(rootId)}`),
	search: (
		q: string,
		mode: 'text' | 'semantic',
		sort: SearchSort,
		match: MatchField | undefined,
		filters: ItemFilters,
		limit = 30,
		offset = 0
	) =>
		request<SearchResult>(
			`/api/search${query({ q, mode, sort, match, ...filters, limit, offset })}`
		),
	chats: (platform: string) => request<Chat[]>(`/api/chats/${platform}`),
	facets: () => request<Record<string, Facets>>('/api/facets'),
	stats: () => request<ArchiveStats[]>('/api/archive/stats'),
	authors: (platforms: string | undefined, prefix: string) =>
		request<{ author: string; items: number }[]>(
			`/api/authors${query({ platforms, prefix, limit: 8 })}`
		),
	recoverMedia: (platform: string, id: string) =>
		request<Item>(`/api/items/${platform}/${encodeURIComponent(id)}/recover-media`, {
			method: 'POST'
		}),
	mediaUrl: (platform: string, id: string, index: number) =>
		`/api/media/${platform}/${encodeURIComponent(id)}/${index}`,

	/* Control plane */
	status: () => request<ControlStatus>('/api/status'),
	run: (body: Record<string, unknown>) =>
		post<{ started: string }>('/api/run', body),
	cycle: (platform: string) =>
		request<{ started: string }>(`/api/cycle/${platform}`, { method: 'POST' }),
	schedule: (body: {
		platform: string;
		enabled: boolean;
		categories: string[];
		interval_minutes: number;
	}) =>
		post<{ started: string }>('/api/schedule', body),
	sources: () => request<SourcesResult>('/api/sources'),
	sourceAction: (
		action: 'add' | 'remove' | 'run' | 'enabled',
		body: { platform: string; target: string; kind?: string | null },
		params = ''
	) =>
		post<{ started: string }>(`/api/sources/${action}${params}`, body),
	cancel: (jobId: number) => request<{ started: string }>(`/api/cancel/${jobId}`, { method: 'POST' }),
	pairStart: (platform: string) =>
		request<{ started?: string; error?: string }>(`/api/pair/${platform}`, { method: 'POST' }),
	pairState: (platform: string) =>
		request<{ paired?: boolean; pairing?: boolean; qr?: string }>(`/api/pair/${platform}`)
};
