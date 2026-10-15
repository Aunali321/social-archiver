<script lang="ts">
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import Users from '@lucide/svelte/icons/users';
	import { api, type ProfileHit } from '$lib/api';
	import { displayName, platformLabel, profileHref } from '$lib/format';
	import AuthorLine from './AuthorLine.svelte';
	import Avatar from './Avatar.svelte';
	import Chip from './Chip.svelte';
	import EmptyState from './EmptyState.svelte';
	import PlatformBadge from './PlatformBadge.svelte';
	import Skeleton from './Skeleton.svelte';

	/** Accounts matching every word typed, in any handle, name or bio they have had, most
	 * archived first. Updates as the query changes; an empty query lists the most archived. */
	interface Props {
		query: string;
		platforms: string[];
	}

	let { query, platforms }: Props = $props();

	let platform = $state<string | null>(null);
	let hits: ProfileHit[] = $state([]);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let generation = 0;

	$effect(() => {
		const q = query.trim();
		const only = platform ?? undefined;
		const mine = ++generation;
		loading = true;
		const timer = setTimeout(async () => {
			try {
				const found = await api.profiles(q, only, 40);
				if (mine !== generation) return;
				hits = found;
				error = null;
			} catch (e) {
				if (mine === generation) error = e instanceof Error ? e.message : String(e);
			} finally {
				if (mine === generation) loading = false;
			}
		}, 150);
		return () => clearTimeout(timer);
	});
</script>

{#if platforms.length > 1}
	<div class="flex flex-wrap gap-2" role="group" aria-label="Platform">
		<Chip selected={platform === null} onclick={() => (platform = null)}>All</Chip>
		{#each platforms as name (name)}
			<Chip selected={platform === name} onclick={() => (platform = name)}>{platformLabel(name)}</Chip>
		{/each}
	</div>
{/if}

<div class="mt-4">
	{#if error}
		<EmptyState title="Couldn't search people" detail={error} error>
			{#snippet icon()}<CircleAlert size={28} />{/snippet}
		</EmptyState>
	{:else if loading && hits.length === 0}
		<div class="flex flex-col gap-2" aria-busy="true">
			{#each Array(6), i (i)}
				<div class="flex items-center gap-3 rounded-md bg-surface-container-low px-4 py-3">
					<Skeleton class="size-10 rounded-full" />
					<div class="flex-1">
						<Skeleton class="h-3.5 w-40" />
						<Skeleton class="mt-2 h-3 w-64" />
					</div>
				</div>
			{/each}
		</div>
	{:else if hits.length === 0}
		<EmptyState
			title="No one matches"
			detail="No account in the archive has gone by a handle, name or bio with those words."
		>
			{#snippet icon()}<Users size={28} />{/snippet}
		</EmptyState>
	{:else}
		<ul class="flex flex-col gap-2 {loading ? 'opacity-70' : ''}">
			{#each hits as { author, bio, items } (author.platform + author.profile_id)}
				<li>
					<a
						href={profileHref(author)}
						class="state-layer flex items-center gap-3 rounded-md bg-surface-container-low px-4 py-3"
					>
						<span class="relative shrink-0">
							<Avatar name={displayName(author)} src={author.avatar_url} size={44} />
							<span class="absolute -right-1.5 -bottom-1.5 scale-[0.68]">
								<PlatformBadge platform={author.platform} />
							</span>
						</span>
						<div class="min-w-0 flex-1">
							<AuthorLine {author} linked={false} class="text-label-lg" />
							{#if bio}
								<p class="line-clamp-2 text-body text-on-surface-variant">{bio}</p>
							{/if}
						</div>
						<span class="shrink-0 text-label text-on-surface-variant">{items.toLocaleString()} items</span>
					</a>
				</li>
			{/each}
		</ul>
	{/if}
</div>
