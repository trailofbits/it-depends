run:
	docker run -v "$(shell pwd):/home/dependabot/it-depends" -w /home/dependabot/it-depends -e "GITHUB_ACCESS_TOKEN=${GITHUB_ACCESS_TOKEN}" dependabot/dependabot-core:0.130.3 bundle exec ruby ./dependabot.rb --directory "/" --repo-name "ethereum/go-ethereum"

deps:
	docker run -v "$(shell pwd):/home/dependabot/it-depends" -w /home/dependabot/it-depends dependabot/dependabot-core:0.130.3 bundle install --jobs=8 --path vendor
