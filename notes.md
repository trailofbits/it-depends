# General Notes

## Supported dependencies

Found in https://github.com/dependabot/dependabot-core/blob/main/omnibus/lib/dependabot/omnibus.rb

```
{"pip"=>Dependabot::Python::FileFetcher,
"terraform"=>Dependabot::Terraform::FileFetcher,
"elm"=>Dependabot::Elm::FileFetcher,
"docker"=>Dependabot::Docker::FileFetcher,
"submodules"=>Dependabot::GitSubmodules::FileFetcher,
"github_actions"=>Dependabot::GithubActions::FileFetcher,
"composer"=>Dependabot::Composer::FileFetcher,
"nuget"=>Dependabot::Nuget::FileFetcher,
"gradle"=>Dependabot::Gradle::FileFetcher,
"maven"=>Dependabot::Maven::FileFetcher,
"hex"=>Dependabot::Hex::FileFetcher,
"cargo"=>Dependabot::Cargo::FileFetcher,
"go_modules"=>Dependabot::GoModules::FileFetcher,
"npm_and_yarn"=>Dependabot::NpmAndYarn::FileFetcher,
"dep"=>Dependabot::Dep::FileFetcher,
"bundler"=>Dependabot::Bundler::FileFetcher}
```

## Calculating Deployed Dependencies (Right Now)

We want to be able to say what dependencies are deployed with an application. This is most easily answered by assuming we want to answer the question _now_---What dependencies are deployed with an application if we deployed it _right now_. This answers the question of "What applications are vulnerable today?".

### First-level dependencies

We can easily parse the dependency files and grab a list of top-level dependencies. This is easy.

The hard part comes when we want to know the exact version is used if it were deployed _today_. We don't necessarily need the exact version, but a range for a tool version (if we assume that the default branch isn't deployed by people (lots of reasons to assume this, like package managers only use published versions. We can dig into HEAD commit if we find the latest version is vulnerable to determine whether it's been fixed at HEAD...)).

### N-level dependencies

These dependencies are potentially influenced by the version of parent dependencies **AND** the exact version of an N-level dependency is determined through the interaction between other sibling/parent dependencies (for most languages where multiple dependency versions cannot coexist, although Rust is one notable exception).

These could be calculated on-demand, but that is expensive because it almost certainly requires downloading or at least re-parsing existing dependency files throughout the chain, since new dependencies could be released.

* One alternative option would be to store the constraints that the language's package manager uses internally, but acquiring that information might be difficult.


## Backfilling Deployed Dependencies in the Past

Ideally, we'd want to travel back in time to answer at least two questions:

* What initial, pinned set of dependencies were used at time of release for this version and how long did it take for a vulnerability to appear in the pinned dependencies?

  * NOTE: This requires some version bound information from a CVE to answer

* How long was a specific version of an application exposed to a vulnerable dependency before it was fixed?

As a follow-up to each, how would this vulnerability be resolved? Implicit update (just re-deploy and updated dependency is automatically pulled in) or explicit update (dependency version constraints were such that manual changes to dependency file was required)
