.PHONY: run build run-db deps check-env

run: check-env deps
	docker run -v "$(shell pwd):/home/dependabot/it-depends" -w /home/dependabot/it-depends -e "GITHUB_ACCESS_TOKEN=${GITHUB_ACCESS_TOKEN}" it-depends bundle exec ruby ./dependabot.rb --directory "/" --repo-name "ethereum/go-ethereum"

run-db: check-env deps
	docker run -v "$(shell pwd):/home/dependabot/it-depends" -w /home/dependabot/it-depends -e "GITHUB_ACCESS_TOKEN=${GITHUB_ACCESS_TOKEN}" it-depends bundle exec ruby ./dependabot.rb --directory "/" --repo-name "ethereum/go-ethereum" --use-database

build:
	docker build -t it-depends .

deps: build
	docker run -v "$(shell pwd):/home/dependabot/it-depends" -w /home/dependabot/it-depends it-depends bundle install --jobs=8 --path vendor

check-env:
ifndef GITHUB_ACCESS_TOKEN
	$(error GITHUB_ACCESS_TOKEN is undefined)
endif
