/** Filter state shared by the timeline and search, round-tripped through the URL so a
 * search survives opening an item and coming back. */

import type { ItemFilters, ItemKind, MediaFilter, PostFormat } from './api';

export const SINCE_OPTIONS = [
	{ value: '7', label: 'Past week' },
	{ value: '30', label: 'Past month' },
	{ value: '365', label: 'Past year' },
	{ value: 'custom', label: 'Custom range' }
];

export const MEDIA_OPTIONS: { value: 'any' | MediaFilter; label: string }[] = [
	{ value: 'any', label: 'Any media' },
	{ value: 'image', label: 'Images' },
	{ value: 'video', label: 'Videos' },
	{ value: 'gif', label: 'GIFs' },
	{ value: 'audio', label: 'Audio' },
	{ value: 'sticker', label: 'Stickers' },
	{ value: 'document', label: 'Documents' }
];

/** What a post, a reply and a repost are called on each platform. */
export function kindOptions(platform: string): { value: ItemKind; label: string }[] {
	switch (platform) {
		case 'reddit':
			return [
				{ value: 'post', label: 'Posts' },
				{ value: 'reply', label: 'Comments' }
			];
		case 'whatsapp':
			return [
				{ value: 'post', label: 'Messages' },
				{ value: 'reply', label: 'Replies' }
			];
		case 'instagram':
			return [];
		case 'twitter':
			return [
				{ value: 'post', label: 'Tweets' },
				{ value: 'reply', label: 'Replies' },
				{ value: 'repost', label: 'Retweets' },
				{ value: 'quote', label: 'Quotes' }
			];
		default:
			return [
				{ value: 'post', label: 'Posts' },
				{ value: 'reply', label: 'Replies & comments' },
				{ value: 'repost', label: 'Retweets' },
				{ value: 'quote', label: 'Quotes' }
			];
	}
}

export const FORMAT_OPTIONS: { value: PostFormat; label: string }[] = [
	{ value: 'reel', label: 'Reels' },
	{ value: 'post', label: 'Posts' },
	{ value: 'carousel', label: 'Carousels' }
];

const DAY = 86_400_000;

function isoDay(date: Date): string {
	return date.toISOString().slice(0, 10);
}

// Every URL parameter and the state field it restores
const PARAMS = {
	platform: 'platform',
	category: 'category',
	subreddit: 'subreddit',
	origin: 'origin',
	chat: 'chat',
	format: 'postFormat',
	author: 'author',
	media: 'media',
	kind: 'kind',
	since: 'since',
	from: 'dateFrom',
	to: 'dateTo'
} as const;

export class Filters {
	platform = $state('');
	category = $state('');
	subreddit = $state('');
	origin = $state('');
	chat = $state(''); // a WhatsApp chat id
	postFormat = $state<'' | PostFormat>('');
	author = $state('');
	media = $state<'' | 'any' | MediaFilter>('');
	kind = $state<'' | ItemKind>('');
	since = $state(''); // days back from SINCE_OPTIONS, or 'custom'
	dateFrom = $state(''); // YYYY-MM-DD, with since = 'custom'
	dateTo = $state('');
	hasLink = $state(false);
	seedsOnly = $state(false);

	constructor(params = new URLSearchParams()) {
		for (const [param, field] of Object.entries(PARAMS)) {
			(this[field] as string) = params.get(param) ?? '';
		}
		this.hasLink = params.has('link');
		this.seedsOnly = params.has('seeds');
	}

	/** Category, subreddit, origin, chat and format values belong to one platform. */
	selectPlatform(platform: string) {
		this.platform = this.platform === platform ? '' : platform;
		this.category = this.subreddit = this.origin = this.chat = this.postFormat = '';
		if (!kindOptions(this.platform).some((option) => option.value === this.kind)) this.kind = '';
	}

	/** Refinements beyond the platform, for a collapsed panel's badge. */
	get active(): number {
		const fields = [
			this.category,
			this.subreddit,
			this.origin,
			this.chat,
			this.postFormat,
			this.author,
			this.media,
			this.kind,
			this.since,
			this.hasLink,
			this.seedsOnly
		];
		return fields.filter(Boolean).length;
	}

	get query(): ItemFilters {
		const custom = this.since === 'custom';
		return {
			platforms: this.platform || undefined,
			category: this.category || undefined,
			subreddit: this.subreddit || undefined,
			origin: this.origin || undefined,
			chat: this.chat || undefined,
			post_format: this.postFormat || undefined,
			author: this.author.trim() || undefined,
			has_media: this.media === 'any' || undefined,
			media: this.media && this.media !== 'any' ? this.media : undefined,
			kind: this.kind || undefined,
			has_link: this.hasLink || undefined,
			seeds_only: this.seedsOnly || undefined,
			date_from: custom
				? this.dateFrom || undefined
				: this.since
					? isoDay(new Date(Date.now() - Number(this.since) * DAY))
					: undefined,
			// The range includes its last day; the API's bound is exclusive
			date_to:
				custom && this.dateTo ? isoDay(new Date(Date.parse(this.dateTo) + DAY)) : undefined
		};
	}

	writeTo(params: URLSearchParams) {
		for (const [param, field] of Object.entries(PARAMS)) {
			if (this[field]) params.set(param, this[field]);
		}
		if (this.hasLink) params.set('link', '1');
		if (this.seedsOnly) params.set('seeds', '1');
	}
}
