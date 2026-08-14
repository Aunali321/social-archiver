<script lang="ts">
	interface Props {
		class?: string;
	}

	let { class: className = 'h-4 w-full' }: Props = $props();
</script>

<div class="shimmer rounded-sm {className}" aria-hidden="true"></div>
