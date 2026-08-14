<script lang="ts">
	interface Props {
		label: string;
		value?: string;
		options: { value: string; label: string }[];
		allLabel?: string;
		onchange?: () => void;
	}

	let { label, value = $bindable(''), options, allLabel, onchange }: Props = $props();
</script>

<select
	bind:value
	aria-label={label}
	{onchange}
	class="h-8 cursor-pointer rounded-sm border border-outline-variant bg-transparent px-2 text-label-lg text-on-surface-variant outline-none focus-visible:border-primary"
>
	{#if allLabel != null}<option value="">{allLabel}</option>{/if}
	{#each options as option (option.value)}
		<option value={option.value}>{option.label}</option>
	{/each}
</select>
