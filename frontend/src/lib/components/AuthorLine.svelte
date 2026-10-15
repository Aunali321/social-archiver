<script lang="ts">
	import BadgeCheck from '@lucide/svelte/icons/badge-check';
	import type { Author } from '$lib/api';
	import { formatHandle, profileHref } from '$lib/format';

	/** Name and handle on one line, truncating the handle first. Links to the account's page
	 * unless `linked` is off, which a caller already inside a link must turn off: links do
	 * not nest. */
	interface Props {
		author: Author;
		linked?: boolean;
		class?: string;
	}

	let { author, linked = true, class: className = '' }: Props = $props();

	const href = $derived(linked ? profileHref(author) : null);
	const handle = $derived(formatHandle(author.platform, author.handle));
	const name = $derived(author.display_name || handle);
</script>

{#snippet label()}
	<span class="truncate font-medium text-on-surface {href ? 'group-hover:underline' : ''}">{name}</span>
	{#if author.verified}
		<BadgeCheck size={14} class="shrink-0 self-center text-primary" aria-label="verified" />
	{/if}
	{#if author.display_name}
		<span class="truncate text-on-surface-variant">{handle}</span>
	{/if}
{/snippet}

{#if href}
	<!-- Inside a clickable card: the profile wins over the card's own navigation -->
	<a {href} class="group flex min-w-0 items-baseline gap-1.5 {className}" onclick={(event) => event.stopPropagation()}>
		{@render label()}
	</a>
{:else}
	<span class="flex min-w-0 items-baseline gap-1.5 {className}">{@render label()}</span>
{/if}
