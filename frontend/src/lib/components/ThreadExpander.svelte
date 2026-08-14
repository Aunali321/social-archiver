<script lang="ts">
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import ChevronUp from '@lucide/svelte/icons/chevron-up';
	import { api, type Item } from '$lib/api';
	import ContextCard from './ContextCard.svelte';

	/** "Show thread" in place: fetches the archived thread once and lists the other members
	 * inline, the current item marked by omission. */
	interface Props {
		item: Item;
	}

	let { item }: Props = $props();

	let members = $state<Item[] | null>(null);
	let open = $state(false);
	let loading = $state(false);
	let failed = $state(false);

	async function toggle(event: MouseEvent) {
		event.stopPropagation();
		event.preventDefault();
		if (open) {
			open = false;
			return;
		}
		if (members == null && !loading) {
			loading = true;
			try {
				const thread = await api.thread(item.platform, item.thread_root_id!);
				members = thread.filter((member) => member.item_id !== item.item_id);
			} catch {
				failed = true;
			} finally {
				loading = false;
			}
		}
		open = true;
	}
</script>

<button
	onclick={toggle}
	class="inline-flex cursor-pointer items-center gap-1 text-label-lg text-primary"
>
	{#if open}<ChevronUp size={16} />Hide thread{:else}<ChevronDown size={16} />
		{loading ? 'Loading…' : 'Show thread'}{/if}
</button>

{#if open}
	{#if failed}
		<p class="mt-2 text-label text-error">Couldn't load the thread.</p>
	{:else if members != null}
		{#if members.length === 0}
			<p class="mt-2 text-label text-on-surface-variant">No other tweets of this thread are archived.</p>
		{:else}
			<div class="mt-2 flex flex-col gap-2 border-l-2 border-outline-variant pl-3">
				{#each members as member (member.item_id)}
					<ContextCard item={member} />
				{/each}
			</div>
		{/if}
	{/if}
{/if}
