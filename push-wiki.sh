#!/bin/bash
# Run this script AFTER manually initializing the wiki at:
# https://github.com/agit8or1/clientst0r/wiki
# (Click "Create the first page", save any content, then run this script)

set -e

echo "Pushing user-guide documentation to GitHub wiki..."

WIKI_DIR="/tmp/clientst0r-wiki"

# Check if the wiki is now accessible
if git ls-remote "https://github.com/agit8or1/clientst0r.wiki.git" >/dev/null 2>&1; then
    echo "Wiki is initialized! Pushing documentation..."
    
    # Clone the wiki
    rm -rf /tmp/wiki-push-temp
    git clone "https://github.com/agit8or1/clientst0r.wiki.git" /tmp/wiki-push-temp
    
    # Copy all files from our prepared directory
    cp "$WIKI_DIR"/*.md /tmp/wiki-push-temp/
    
    # Add and commit
    cd /tmp/wiki-push-temp
    git add .
    git status
    
    # Only commit if there are changes
    if git diff --staged --quiet; then
        echo "No changes to commit"
    else
        git commit -m "Add user guide documentation from user-guide/ folder"
        git push origin master || git push origin main
        echo "SUCCESS: Documentation pushed to wiki!"
    fi
    
    cd /
    rm -rf /tmp/wiki-push-temp
else
    echo "ERROR: Wiki is still not initialized."
    echo "Please go to: https://github.com/agit8or1/clientst0r/wiki"
    echo "Click 'Create the first page', add any content, and save."
    echo "Then run this script again."
    exit 1
fi
