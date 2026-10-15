<script lang="ts" generics="T extends string">
	/** M3 primary tabs: switching views of the same place, not navigating away from it. */
	interface Props {
		tabs: { value: T; label: string }[];
		value: T;
		label: string;
		onchange: (value: T) => void;
	}

	let { tabs, value, label, onchange }: Props = $props();
</script>

<div role="tablist" aria-label={label} class="flex border-b border-outline-variant">
	{#each tabs as tab (tab.value)}
		<button
			role="tab"
			aria-selected={tab.value === value}
			onclick={() => onchange(tab.value)}
			class="state-layer relative h-12 flex-1 cursor-pointer text-label-lg sm:flex-none sm:px-6 {tab.value === value
				? 'text-primary'
				: 'text-on-surface-variant'}"
		>
			{tab.label}
			{#if tab.value === value}
				<span class="absolute inset-x-4 bottom-0 h-[3px] rounded-t-full bg-primary"></span>
			{/if}
		</button>
	{/each}
</div>
