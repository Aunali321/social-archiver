<script lang="ts">
	import BadgeCheck from '@lucide/svelte/icons/badge-check';
	import Calendar from '@lucide/svelte/icons/calendar';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import Link2 from '@lucide/svelte/icons/link-2';
	import Lock from '@lucide/svelte/icons/lock';
	import MapPin from '@lucide/svelte/icons/map-pin';
	import type { Profile } from '$lib/api';
	import { displayName, formatCount, formatHandle, platformLabel } from '$lib/format';
	import Avatar from './Avatar.svelte';
	import PlatformBadge from './PlatformBadge.svelte';

	/** An account as it shows itself now: the newest version, the counts the platform last
	 * reported, and what is known about whether it still exists. */
	interface Props {
		profile: Profile;
	}

	let { profile }: Props = $props();

	const current = $derived(profile.versions[0]);
	const name = $derived(displayName(current));
	const counts = $derived(
		[
			[profile.followers, 'followers'],
			[profile.following, 'following'],
			[profile.posts, 'posts']
		].filter(([count]) => count != null) as [number, string][]
	);
	const STATUS: Record<string, string> = { suspended: 'Suspended', deleted: 'Deleted' };
</script>

<section aria-labelledby="profile-name" class="overflow-hidden rounded-lg bg-surface-container-low">
	<div
		class="aspect-[3/1] w-full bg-surface-container-high"
		style={current.banner_url ? '' : `background: var(--tint-${profile.platform}-bg)`}
	>
		{#if current.banner_url}
			<img src={current.banner_url} alt="" class="size-full object-cover" decoding="async" />
		{/if}
	</div>

	<div class="px-5 pb-5">
		<div class="-mt-10 flex items-end justify-between gap-3">
			<span class="relative rounded-full ring-4 ring-surface-container-low">
				<Avatar {name} src={current.avatar_url} size={80} />
				<span class="absolute -right-1 -bottom-1"><PlatformBadge platform={profile.platform} /></span>
			</span>
			<a
				href={profile.url}
				target="_blank"
				rel="noopener noreferrer"
				class="state-layer inline-flex h-10 items-center gap-2 rounded-full border border-outline-variant px-4 text-label-lg text-on-surface"
			>
				<ExternalLink size={16} /> {platformLabel(profile.platform)}
			</a>
		</div>

		<h1 id="profile-name" class="mt-3 flex items-center gap-1.5 text-title-lg text-on-surface">
			<span class="truncate">{name}</span>
			{#if current.verified}<BadgeCheck size={20} class="shrink-0 text-primary" aria-label="verified" />{/if}
			{#if current.private}<Lock size={16} class="shrink-0 text-on-surface-variant" aria-label="private" />{/if}
		</h1>
		<p class="flex flex-wrap items-center gap-2 text-body text-on-surface-variant">
			{formatHandle(profile.platform, current.handle)}
			{#if profile.is_self}
				<span class="rounded-sm bg-primary-container px-2 py-0.5 text-label text-on-primary-container">You</span>
			{/if}
			{#if STATUS[profile.status]}
				<span class="rounded-sm bg-error-container px-2 py-0.5 text-label text-on-error-container">
					{STATUS[profile.status]}
				</span>
			{/if}
		</p>

		{#if current.bio}
			<p class="mt-3 text-body-lg break-words whitespace-pre-line text-on-surface">{current.bio}</p>
		{/if}

		<ul class="mt-3 flex flex-wrap gap-x-4 gap-y-1.5 text-body text-on-surface-variant">
			{#if current.location}
				<li class="inline-flex items-center gap-1"><MapPin size={14} />{current.location}</li>
			{/if}
			{#if current.website}
				<li class="inline-flex min-w-0 items-center gap-1">
					<Link2 size={14} class="shrink-0" />
					<a href={current.website} target="_blank" rel="noopener noreferrer" class="truncate text-primary hover:underline">
						{current.website.replace(/^https?:\/\/(www\.)?/, '')}
					</a>
				</li>
			{/if}
			{#if profile.joined_at}
				<li class="inline-flex items-center gap-1">
					<Calendar size={14} />Joined {new Date(profile.joined_at).toLocaleDateString(undefined, { month: 'long', year: 'numeric' })}
				</li>
			{/if}
		</ul>

		{#if counts.length}
			<dl class="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-body">
				{#each counts as [count, label] (label)}
					<div class="flex gap-1">
						<dt class="sr-only">{label}</dt>
						<dd class="font-medium text-on-surface">{formatCount(count)}</dd>
						<span class="text-on-surface-variant" aria-hidden="true">{label}</span>
					</div>
				{/each}
			</dl>
		{/if}
	</div>
</section>
