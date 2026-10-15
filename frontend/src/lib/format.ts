/** Display helpers shared across pages. */

export function formatCount(n: number | null | undefined): string {
	if (n == null) return '';
	if (n < 1000) return String(n);
	if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}K`;
	return `${(n / 1_000_000).toFixed(1)}M`;
}

export function formatDate(iso: string | null): string {
	if (!iso) return '';
	const date = new Date(iso);
	const now = new Date();
	const sameYear = date.getFullYear() === now.getFullYear();
	return date.toLocaleDateString(undefined, {
		month: 'short',
		day: 'numeric',
		...(sameYear ? {} : { year: 'numeric' })
	});
}

export function formatDateTime(iso: string | null): string {
	if (!iso) return '';
	return new Date(iso).toLocaleString(undefined, {
		year: 'numeric',
		month: 'short',
		day: 'numeric',
		hour: '2-digit',
		minute: '2-digit'
	});
}

export function formatTime(iso: string | null): string {
	if (!iso) return '';
	return new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

/** Chat-day key for a message, matching the archiver's thread_root_id day grouping (UTC). */
export function dayKey(iso: string | null): string {
	return iso ? iso.slice(0, 10) : '';
}

export function formatDay(day: string): string {
	const date = new Date(`${day}T00:00:00Z`);
	return date.toLocaleDateString(undefined, {
		weekday: 'short',
		year: 'numeric',
		month: 'long',
		day: 'numeric',
		timeZone: 'UTC'
	});
}

/** Stable per-author hue for generated avatars and chat sender names. */
export function authorHue(name: string): number {
	let hash = 0;
	for (const ch of name) hash = (hash * 31 + ch.charCodeAt(0)) | 0;
	return Math.abs(hash) % 360;
}

export const PLATFORM_LABELS: Record<string, string> = {
	twitter: 'Twitter',
	reddit: 'Reddit',
	instagram: 'Instagram',
	whatsapp: 'WhatsApp'
};

export function platformLabel(platform: string): string {
	return PLATFORM_LABELS[platform] ?? platform;
}

/** A handle the way its platform writes one. */
export function formatHandle(platform: string, handle: string): string {
	switch (platform) {
		case 'reddit':
			return `u/${handle}`;
		case 'whatsapp':
			return /^\d+$/.test(handle) ? `+${handle}` : handle;
		default:
			return `@${handle}`;
	}
}

/** Where an account's own page lives in the archive; null for an item no account stands behind. */
export function profileHref(author: { platform: string; profile_id: string | null }): string | null {
	return author.profile_id ? `/profile/${author.platform}/${encodeURIComponent(author.profile_id)}` : null;
}

/** What to call an account: its own name, else its handle. */
export function displayName(author: { display_name: string | null; handle: string }): string {
	return author.display_name || author.handle;
}
