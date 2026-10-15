<script lang="ts">
	import { api, type ProfileHit } from '$lib/api';
	import { formatHandle } from '$lib/format';

	/** An author with suggestions from the archive's accounts, busiest first, matched on any
	 * handle or name they have gone by. Commits on Enter, on leaving the field, or on picking
	 * a suggestion. */
	interface Props {
		value?: string;
		platform: string;
		onchange: () => void;
	}

	let { value = $bindable(''), platform, onchange }: Props = $props();

	const id = $props.id();
	let suggestions: ProfileHit[] = $state([]);
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
		if (suggestions.some((s) => s.author.handle === value)) {
			commit();
			return;
		}
		const mine = ++generation;
		timer = setTimeout(async () => {
			const found = await api.profiles(value.trim(), platform || undefined, 8);
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
	{#each suggestions as { author, items } (author.platform + author.profile_id)}
		<option value={author.handle}>
			{author.display_name ?? formatHandle(author.platform, author.handle)} · {items.toLocaleString()} items
		</option>
	{/each}
</datalist>
