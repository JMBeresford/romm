<script setup lang="ts">
import type { Rom } from "@/stores/roms";

defineProps<{ rom: Rom }>();
</script>

<template>
  <div rounded="0" class="table">
    <v-row
      v-if="rom.igdb_id"
      class="align-center justify-center pa-2"
      no-gutters
    >
      <v-col class="text-center">
        <span>Source</span>
      </v-col>
      <v-col class="text-center">
        <span>ID</span>
      </v-col>
      <v-col class="text-center">
        <span>Rating</span>
      </v-col>
    </v-row>
    <v-divider />
    <v-row
      v-if="rom.igdb_id"
      class="align-center justify-center pa-2"
      no-gutters
    >
      <v-col class="text-center">
        <v-chip variant="outlined" class="text-romm-accent-1" @click="" label>
          <a
            style="text-decoration: none; color: inherit"
            href=https://www.igdb.com
            target="_blank"
            >IGDB</a
          >
        </v-chip>
      </v-col>
      <v-col class="text-center">
        <v-chip variant="tonal" class="text-romm-accent-1" @click="" label>
          <a
            style="text-decoration: none; color: inherit"
            :href="`https://www.igdb.com/games/${rom.slug}`"
            target="_blank"
            >{{ rom.igdb_id }}</a
          >
        </v-chip>
      </v-col>
      <v-col class="text-center">
        <v-chip variant="text" label>
          {{ rom.total_rating }}
        </v-chip>
      </v-col>
    </v-row>
  </div>
</template>
<style scoped>
.table {
  border: 1px solid rgba(var(--v-theme-primary));
}
</style>