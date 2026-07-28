<script setup>
import RepoList from '@theme/RepoList.vue'

const topics = {
  "trame-maintenance-program": "Maintenance program",
  "vue2": "Vue2",
  "vue3": "Vue3",
  "...": "Community",
}
</script>

# Widget catalog

<RepoList filter-topic="trame-widget" :displayable-topics="topics" />