<script lang="ts">
	import { page } from '$app/state';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import { api, type Profile } from '$lib/api';
	import { displayName, formatDate } from '$lib/format';
	import Chip from '$lib/components/Chip.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ItemFeed from '$lib/components/ItemFeed.svelte';
	import ProfileHeader from '$lib/components/ProfileHeader.svelte';
	import ProfileHistory from '$lib/components/ProfileHistory.svelte';
	import Skeleton from '$lib/components/Skeleton.svelte';

	const platform = $derived(page.params.platform!);
	const profileId = $derived(decodeURIComponent(page.params.id!));

	let profile = $state<Profile | null>(null);
	let error = $state<string | null>(null);
	let origin = $state<string | null>(null);

	$effect(() => {
		profile = null;
		error = null;
		origin = null;
		api
			.profile(platform, profileId)
			.then((p) => (profile = p))
			.catch((e) => (error = e instanceof Error ? e.message : String(e)));
	});

	const origins = $derived(profile ? Object.entries(profile.encounters.origins) : []);
</script>

<svelte:head><title>{profile ? displayName(profile.versions[0]) : 'Profile'} · Archive</title></svelte:head>

<div class="mx-auto max-w-6xl px-4 pt-4">
	<button
		onclick={() => history.back()}
		class="state-layer mb-3 inline-flex h-10 cursor-pointer items-center gap-2 rounded-full px-3 text-label-lg text-on-surface-variant"
	>
		<ArrowLeft size={18} /> Back
	</button>

	{#if error}
		<EmptyState title="Couldn't load this profile" detail={error} error>
			{#snippet icon()}<CircleAlert size={28} />{/snippet}
		</EmptyState>
	{:else if !profile}
		<div class="mx-auto max-w-2xl overflow-hidden rounded-lg bg-surface-container-low" aria-busy="true">
			<Skeleton class="aspect-[3/1] w-full rounded-none" />
			<div class="px-5 pb-5">
				<Skeleton class="-mt-10 size-20 rounded-full" />
				<Skeleton class="mt-4 h-5 w-48" />
				<Skeleton class="mt-2 h-4 w-32" />
				<Skeleton class="mt-4 h-4 w-full" />
			</div>
		</div>
	{:else}
		<div class="lg:grid lg:grid-cols-[22rem_minmax(0,1fr)] lg:items-start lg:gap-8 xl:grid-cols-[24rem_minmax(0,46rem)] xl:justify-center">
			<aside class="flex flex-col gap-3 lg:sticky lg:top-4">
				<ProfileHeader {profile} />
				{#if profile.versions.length > 1}
					<ProfileHistory platform={profile.platform} versions={profile.versions.slice(1)} />
				{/if}
			</aside>

			<section aria-labelledby="archived" class="mt-6 min-w-0 lg:mt-0">
				<h2 id="archived" class="text-title text-on-surface">
					{profile.encounters.total.toLocaleString()} archived
				</h2>
				{#if profile.encounters.first}
					<p class="text-label text-on-surface-variant">
						{formatDate(profile.encounters.first)} – {formatDate(profile.encounters.last)}
					</p>
				{/if}
				{#if origins.length > 1}
					<div class="mt-3 flex flex-wrap gap-2" role="group" aria-label="How these were archived">
						<Chip selected={origin === null} onclick={() => (origin = null)}>All</Chip>
						{#each origins as [name, count] (name)}
							<Chip selected={origin === name} onclick={() => (origin = name)}>
								{name} · {count.toLocaleString()}
							</Chip>
						{/each}
					</div>
				{/if}
				<div class="mt-4">
					{#key `${platform}/${profileId}/${origin}`}
						<ItemFeed
							load={(cursor) =>
								api.items({ platforms: platform, author_id: profileId, origin: origin ?? undefined }, cursor)}
							errorTitle="Couldn't load their items"
							emptyDetail="None of this account's items are in the archive."
						/>
					{/key}
				</div>
			</section>
		</div>
	{/if}
</div>
