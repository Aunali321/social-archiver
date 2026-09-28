<script lang="ts">
	import { api } from '$lib/api';

	/** An author name with suggestions from the archive, busiest first. Commits on Enter,
	 * on leaving the field, or on picking a suggestion. */
	interface Props {
		value?: string;
		platform: string;
		onchange: () => void;
	}

	let { value = $bindable(''), platform, onchange }: Props = $props();

	const id = $props.id();
	let suggestions: { author: string; items: number }[] = $state([]);
	let committed = value;
	let timer: ReturnType<typeof setTimeout>;
	let generation = 0;

	function commit() {
		if (value.trim() === committed.trim()) return;
		committed = value;
		onchange();
	}

	function suggest() {
		clearTimeout(timer);
		if (suggestions.some((s) => s.author === value)) {
			commit();
			return;
		}
		const mine = ++generation;
		timer = setTimeout(async () => {
			const found = await api.authors(platform || undefined, value.trim());
			if (mine === generation) suggestions = found;
		}, 150);
	}
</script>

<input
	type="text"
	bind:value
	list="{id}-authors"
	aria-label="Author"
	placeholder="Author"
	autocomplete="off"
	oninput={suggest}
	onfocus={suggest}
	onchange={commit}
	class="h-8 w-44 rounded-sm border border-outline-variant bg-transparent px-2 text-label-lg text-on-surface outline-none placeholder:text-on-surface-variant focus-visible:border-primary"
/>
<datalist id="{id}-authors">
	{#each suggestions as suggestion (suggestion.author)}
		<option value={suggestion.author}>{suggestion.items.toLocaleString()} items</option>
	{/each}
</datalist>
