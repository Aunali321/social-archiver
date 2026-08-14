<script lang="ts">
	import { page } from '$app/state';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import { api, type Item } from '$lib/api';
	import ItemCard from '$lib/components/ItemCard.svelte';
	import Skeleton from '$lib/components/Skeleton.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';

	let members = $state<Item[] | null>(null);
	let error = $state<string | null>(null);

	const platform = $derived(page.params.platform!);
	const rootId = $derived(decodeURIComponent(page.params.root!));

	$effect(() => {
		members = null;
		error = null;
		api
			.thread(platform, rootId)
			.then((items) => (members = items))
			.catch((e) => (error = e instanceof Error ? e.message : String(e)));
	});
</script>

<svelte:head><title>Thread · Archive</title></svelte:head>

<div class="mx-auto max-w-2xl px-4 pt-4">
	<button
		onclick={() => history.back()}
		class="state-layer mb-3 inline-flex h-10 cursor-pointer items-center gap-2 rounded-full px-3 text-label-lg text-on-surface-variant"
	>
		<ArrowLeft size={18} /> Back
	</button>

	<h1 class="mb-4 text-headline text-on-surface">
		{platform === 'whatsapp' ? 'Chat day' : 'Thread'}
	</h1>

	{#if error}
		<EmptyState title="Couldn't load the thread" detail={error} error>
			{#snippet icon()}<CircleAlert size={28} />{/snippet}
		</EmptyState>
	{:else if members == null}
		<div class="flex flex-col gap-3">
			{#each Array(4), i (i)}
				<div class="rounded-md bg-surface-container-low p-4">
					<Skeleton class="h-3.5 w-40" />
					<Skeleton class="mt-3 h-3.5 w-full" />
				</div>
			{/each}
		</div>
	{:else}
		<p class="mb-3 text-label text-on-surface-variant">{members.length} archived item(s), oldest first</p>
		<div class="flex flex-col gap-3">
			{#each members as member (member.item_id)}
				<ItemCard item={member} />
			{/each}
		</div>
	{/if}
	<div class="h-8"></div>
</div>
