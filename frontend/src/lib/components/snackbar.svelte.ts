/** App-wide transient messages, rendered by the layout's Snackbar host. */

interface SnackbarMessage {
	id: number;
	text: string;
	error: boolean;
}

let nextId = 0;

export const snackbars: SnackbarMessage[] = $state([]);

export function toast(text: string, options: { error?: boolean } = {}) {
	const message = { id: nextId++, text, error: options.error ?? false };
	snackbars.push(message);
	setTimeout(() => {
		const index = snackbars.findIndex((m) => m.id === message.id);
		if (index !== -1) snackbars.splice(index, 1);
	}, 5000);
}
