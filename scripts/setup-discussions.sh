#!/usr/bin/env bash
# Setup GitHub Discussions categories and welcome post for sendspin-bt-bridge
# Usage: ./scripts/setup-discussions.sh [owner/repo]
#
# Requires: gh CLI authenticated
# Note: GitHub GraphQL API is used because REST API doesn't support discussion categories

set -euo pipefail

REPO="${1:-trudenboy/sendspin-bt-bridge}"
OWNER="${REPO%%/*}"
REPO_NAME="${REPO##*/}"

echo "Setting up Discussions for $REPO"
echo ""

# Get repository ID
REPO_ID=$(gh api graphql -f query="
  { repository(owner: \"$OWNER\", name: \"$REPO_NAME\") { id } }
" --jq '.data.repository.id')

echo "Repository ID: $REPO_ID"

# Get existing category slugs
echo ""
echo "📂 Existing categories:"
EXISTING=$(gh api graphql -f query="
  { repository(owner: \"$OWNER\", name: \"$REPO_NAME\") {
    discussionCategories(first: 20) {
      nodes { id name slug isAnswerable }
    }
  } }
" --jq '.data.repository.discussionCategories.nodes[] | "\(.slug)|\(.id)|\(.name)|\(.isAnswerable)"')

while IFS='|' read -r slug id name answerable; do
  echo "  $name ($slug) [answerable=$answerable] id=$id"
done <<< "$EXISTING"

# Helper: check if category exists
category_exists() {
  echo "$EXISTING" | grep -q "^$1|"
}

# Helper: get category ID by slug
category_id() {
  echo "$EXISTING" | grep "^$1|" | cut -d'|' -f2
}

echo ""
echo "🔧 Creating new categories..."
echo ""
echo "  ⚠️  GitHub API does not support creating/deleting Discussion categories."
echo "  Please create these manually in repository Settings → Discussions:"
echo ""
echo "  1. ➕ Create 'Bluetooth & Audio' (emoji: 🔊, format: Question/Answer)"
echo "     Description: Speaker compatibility, pairing, codecs, audio routing, BT adapter questions"
echo ""
echo "  2. ➕ Create 'Deployment' (emoji: 🏗️, format: Question/Answer)"
echo "     Description: HA Addon, Docker Compose, Proxmox LXC, OpenWrt LXC setup and configuration"
echo ""
echo "  3. 🗑️  Delete 'Polls' category (if not needed)"
echo ""

# Create Welcome post in General
echo ""
echo "📝 Creating Welcome post..."

GENERAL_ID=$(category_id "general")

if [ -z "$GENERAL_ID" ]; then
  echo "  ⚠️  General category not found, skipping welcome post"
else
  WELCOME_BODY='## 👋 Welcome to Sendspin BT Bridge Discussions!

This is the place to ask questions, share ideas, and show off your setup.

### Where to post

| Your question | Where to go |
|---------------|-------------|
| 🐛 Something is broken (reproducible bug) | [New Issue → Bug Report](https://github.com/trudenboy/sendspin-bt-bridge/issues/new?template=bug_report.yml) |
| 🔊 Bluetooth won'\''t pair / no audio / crackling | **Bluetooth & Audio** category |
| 🏗️ Can'\''t set up Docker / HA Addon / LXC | **Deployment** category |
| 🙏 General usage question | **Q&A** category |
| 💡 New feature idea | **Ideas** category |
| 🛠️ Want to show your setup | **Show and Tell** category |

### Guidelines

1. **Search first** — check [documentation](https://trudenboy.github.io/sendspin-bt-bridge/) and existing discussions before posting
2. **Be specific** — include your deployment method, bridge version, OS, and BT adapter model
3. **Include logs** — attach `docker logs` or `journalctl` output for troubleshooting questions
4. **For ideas** — describe your use case (why you need it), not just what you want
5. **Language** — English preferred, Russian accepted
6. **Be kind** — this is a community project, help each other out 🤝

### Useful links

- 📖 [Documentation](https://trudenboy.github.io/sendspin-bt-bridge/)
- 🐙 [GitHub Repository](https://github.com/trudenboy/sendspin-bt-bridge)
- 🎵 [Music Assistant Discussion](https://github.com/orgs/music-assistant/discussions/5061)
- 🐛 [Report a Bug](https://github.com/trudenboy/sendspin-bt-bridge/issues/new/choose)'

  gh api graphql -f query="
    mutation {
      createDiscussion(input: {
        repositoryId: \"$REPO_ID\"
        categoryId: \"$GENERAL_ID\"
        title: \"👋 Welcome — Read this before posting\"
        body: $(echo "$WELCOME_BODY" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))')
      }) { discussion { url } }
    }
  " --jq '.data.createDiscussion.discussion.url' && echo "  ✅ Welcome post created" || echo "  ⚠️  Could not create welcome post (may already exist)"
fi

echo ""
echo "Done! 🎉"
echo ""
echo "Manual steps remaining:"
echo "  1. Pin the Welcome post in Discussions"
echo "  2. Review category order in Settings → Discussions"
