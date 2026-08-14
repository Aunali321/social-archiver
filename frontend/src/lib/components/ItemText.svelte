<script lang="ts">
	/** Body text, optionally rendered from a search snippet whose [ ] markers become <mark>s. */
	interface Props {
		text: string | null;
		snippet?: string | null;
		clamp?: number; // line-clamp; 0 = no clamp
		class?: string;
	}

	let { text, snippet = null, clamp = 0, class: className = '' }: Props = $props();

	const parts = $derived.by(() => {
		if (snippet) {
			return snippet
				.split(/(\[[^\[\]]{1,80}\])/g)
				.filter(Boolean)
				.map((part) =>
					part.startsWith('[') && part.endsWith(']')
						? { text: part.slice(1, -1), mark: true }
						: { text: part, mark: false }
				);
		}
		return text ? [{ text, mark: false }] : [];
	});
</script>

{#if parts.length}
	<p
		class="text-body break-words whitespace-pre-line text-on-surface {className}"
		style={clamp ? `display: -webkit-box; -webkit-line-clamp: ${clamp}; -webkit-box-orient: vertical; overflow: hidden` : ''}
	>
		{#each parts as part, i (i)}
			{#if part.mark}<mark class="rounded-xs bg-primary-container px-0.5 text-on-primary-container"
					>{part.text}</mark
				>{:else}{part.text}{/if}
		{/each}
	</p>
{/if}
