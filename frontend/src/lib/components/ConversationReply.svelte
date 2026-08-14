<script lang="ts">
	import Heart from '@lucide/svelte/icons/heart';
	import ConversationReply from './ConversationReply.svelte';
	import Avatar from './Avatar.svelte';
	import ItemText from './ItemText.svelte';
	import MediaStrip from './MediaStrip.svelte';
	import EngagementRow from './EngagementRow.svelte';
	import type { ConversationNode } from '$lib/api';
	import { formatDate } from '$lib/format';

	/** One post in a conversation, Twitter-shaped: avatar column, bold author, full content.
	 * Its own replies nest beneath with a connector. */
	interface Props {
		node: ConversationNode;
	}

	let { node }: Props = $props();
	const item = $derived(node.item);
</script>

<div>
	<a
		href="/item/{item.platform}/{encodeURIComponent(item.item_id)}"
		class="state-layer flex gap-3 rounded-md px-3 py-3 {item.is_seed
			? 'bg-surface-container'
			: 'bg-transparent'}"
	>
		<Avatar name={item.author_username} size={40} />
		<div class="min-w-0 flex-1">
			<p class="flex items-baseline gap-2">
				<span class="truncate text-label-lg font-medium text-on-surface">
					{item.author_username}
				</span>
				{#if item.is_seed}
					<span
						class="inline-flex shrink-0 items-center gap-1 rounded-full bg-primary-container px-2 py-0.5 text-label text-on-primary-container"
					>
						<Heart size={10} />{item.origin ?? item.category}
					</span>
				{/if}
				<span class="ml-auto shrink-0 text-label text-on-surface-variant">
					{formatDate(item.created_at)}
				</span>
			</p>
			<ItemText text={item.text} class="mt-1 !text-body-lg" />
			{#if item.media.length}
				<div class="mt-2"><MediaStrip {item} /></div>
			{/if}
			<EngagementRow {item} class="mt-2" />
		</div>
	</a>

	{#if node.replies.length}
		<div class="mt-1 ml-[1.2rem] flex flex-col gap-1 border-l-2 border-outline-variant pl-4">
			{#each node.replies as reply (reply.item.item_id)}
				<ConversationReply node={reply} />
			{/each}
		</div>
	{/if}
</div>
