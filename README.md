# SIEVE Blockchain Dependency Info

To run this, simply execute the following after creating and pasting your GitHub access token:

```bash
docker pull dependabot/dependabot-core:0.130.3

docker run -v "$(pwd):/home/dependabot/depends-db" \
    -w /home/dependabot/depends-db \
    dependabot/dependabot-core:0.130.3 \
    bundle install --jobs=8 --path vendor

docker run -v "$(pwd):/home/dependabot/depends-db" \
    -w /home/dependabot/depends-db \
    -e "GITHUB_ACCESS_TOKEN=YOUR_TOKEN" \
    dependabot/dependabot-core:0.130.3 \
    bundle exec ruby ./dependabot.rb --directory "/" --repo-name "ethereum/go-ethereum"
```

The `--directory` option is to help point dependabot to the directory where the dependency file(s) reside.

The `--repo-name` option is the path to a GitHub repo. More directions on how to look at other repos will be updated later.

By default, the script tries looking for all dependabot-supported dependency files.

## Dev environment

Use VS Code and select to reopen in dev container when it pops up.

## TODO for this script

* Save results in a database of some kind.

* Figure out the best way to process a large number of GitHub repos:

  * Check for all dependency files in parallel within the script

  * The startup time for the script is pretty long... Might be more efficient to process `n` repos per script run instead of one at a time.

* Some way to prevent redownloading a GitHub repo based on some property (same SHA, dependency file(s) haven't changed, etc.)

## Other notes

See [notes.md](./notes.md) for more general notes about what to do with this information.

Since I have zero experience with Ruby other than this, I think post-processing the results with Python would be best for anything that isn't easy to grab from Dependabot.

## Resources

Starter scripts - https://github.com/dependabot/dependabot-script
