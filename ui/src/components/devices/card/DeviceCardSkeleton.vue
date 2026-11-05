<template>
  <article
    data-device-card-skeleton
    class="flex flex-col gap-3 rounded-(--radius-card) border border-border bg-surface-card p-4"
    aria-hidden="true"
  >
    <div class="flex items-start gap-2">
      <span class="size-8 shrink-0 animate-pulse rounded-full bg-surface-secondary" />
      <div class="min-w-0 flex-1 space-y-2">
        <span class="block h-4 w-2/3 animate-pulse rounded bg-surface-secondary" />
        <span class="block h-3 w-1/2 animate-pulse rounded bg-surface-secondary" />
      </div>
      <span class="h-5 w-20 animate-pulse rounded-full bg-surface-secondary" />
    </div>
    <span class="block h-5 w-3/4 animate-pulse rounded bg-surface-secondary" />
    <span class="mt-2 block h-8 w-24 animate-pulse rounded-(--radius-button) bg-surface-secondary" />
  </article>
</template>
