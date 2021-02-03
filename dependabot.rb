# This script is designed to loop through all dependencies in a GHE, GitLab or
# Azure DevOps project

require "date"
require "neo4j_ruby_driver"
require "active_graph"
require "dependabot/file_fetchers"
require "dependabot/file_parsers"
require "dependabot/update_checkers"
require "dependabot/file_updaters"
require "dependabot/pull_request_creator"
require "dependabot/omnibus"
require "gitlab"

require "optparse"

options = {}
OptionParser.new do |opts|
  opts.banner = "Usage: --repo-name NAME --directory DIR"

  opts.on("-r", "--repo-name NAME", "Github Repo Name") { |v| options[:repo_name] = v }
  opts.on("-d", "--directory DIR", "Directory with dependency files") { |v| options[:directory] = v }
  opts.on("--use-database", "Use a database") { |v| options[:use_database] = v }
end.parse!

repo_name = options[:repo_name]
directory = options[:directory]
use_database = options[:use_database]

puts repo_name
puts directory

credentials = [
  {
    "type" => "git_source",
    "host" => "github.com",
    "username" => "x-access-token",
    "password" => ENV["GITHUB_ACCESS_TOKEN"], # A GitHub access token with read access to public repos
  },
]

# NOTE(ek): Not sure if there is an API to get all available resolvers
available_package_managers = [
  "pip",
  "nuget",
  "elm",
  "gradle",
  "maven",
  "hex",
  "cargo",
  "go_modules",
  "npm_and_yarn",
  "dep",
  "bundler",
]

# TODO(ek): Make this functional and optional
# Output a table of current connections to the DB
if use_database
  database_host = "host.docker.internal"
  database_port = 7687
  # Currently not able to select anything other than default database
  database_name = "dependabot"
  database_user = "neo4j"
  database_password = "password"
  ActiveGraph::Base.driver =
    Neo4j::Driver::GraphDatabase.driver("bolt://#{database_host}:#{database_port}",
                                        Neo4j::Driver::AuthTokens.basic(database_user,
                                                                        database_password),
                                        encryption: false)

  def create_constraint(label_name, property, options = {})
    begin
      ActiveGraph::Base.label_object(label_name).create_constraint(property, options)
      ActiveGraph::ModelSchema.reload_models_data!
    rescue
      puts "Using existing index"
    end
  end

  def create_index(label_name, property, options = {})
    begin
      ActiveGraph::Base.label_object(label_name).create_index(property, options)
      ActiveGraph::ModelSchema.reload_models_data!
    rescue
      puts "Using existing index"
    end
  end

  # This is supposedly doable automatically via rake actions :shrug:
  create_constraint(:Project, :uuid, type: :unique)
  create_constraint(:Version, :uuid, type: :unique)
  create_index(:Project, :title, type: :exact)
  # ActiveGraph::Base.query('CREATE CONSTRAINT ON (p:Project) ASSERT p.uuid IS UNIQUE')
  class Project
    include ActiveGraph::Node

    property :title, type: String
    property :uri, type: String
    include ActiveGraph::Timestamps # will give model created_at and updated_at timestamps

    has_many :in, :dependents, origin: :dependencies, model_class: :Version
    has_many :out, :versions, type: :HAS_VERSION, model_class: :Version, unique: { on: :version }
  end

  class Version
    include ActiveGraph::Node

    property :version, type: String
    property :release_date, type: DateTime
    include ActiveGraph::Timestamps

    has_one :in, :project, origin: :versions
    has_many :out, :dependencies, type: :DEPENDS_ON, model_class: :Project, unique: :all
  end
end

if ENV["GITHUB_ENTERPRISE_ACCESS_TOKEN"]
  credentials << {
    "type" => "git_source",
    "host" => ENV["GITHUB_ENTERPRISE_HOSTNAME"], # E.g., "ghe.mydomain.com",
    "username" => "x-access-token",
    "password" => ENV["GITHUB_ENTERPRISE_ACCESS_TOKEN"], # A GHE access token with API permission
  }

  source = Dependabot::Source.new(
    provider: "github",
    hostname: ENV["GITHUB_ENTERPRISE_HOSTNAME"],
    api_endpoint: "https://#{ENV["GITHUB_ENTERPRISE_HOSTNAME"]}/api/v3/",
    repo: repo_name,
    directory: directory,
    branch: nil,
  )
elsif ENV["GITLAB_ACCESS_TOKEN"]
  gitlab_hostname = ENV["GITLAB_HOSTNAME"] || "gitlab.com"

  credentials << {
    "type" => "git_source",
    "host" => gitlab_hostname,
    "username" => "x-access-token",
    "password" => ENV["GITLAB_ACCESS_TOKEN"], # A GitLab access token with API permission
  }

  source = Dependabot::Source.new(
    provider: "gitlab",
    hostname: gitlab_hostname,
    api_endpoint: "https://#{gitlab_hostname}/api/v4",
    repo: repo_name,
    directory: directory,
    branch: nil,
  )
elsif ENV["AZURE_ACCESS_TOKEN"]
  azure_hostname = ENV["AZURE_HOSTNAME"] || "dev.azure.com"

  credentials << {
    "type" => "git_source",
    "host" => azure_hostname,
    "username" => "x-access-token",
    "password" => ENV["AZURE_ACCESS_TOKEN"],
  }

  source = Dependabot::Source.new(
    provider: "azure",
    hostname: azure_hostname,
    api_endpoint: "https://#{azure_hostname}/",
    repo: repo_name,
    directory: directory,
    branch: nil,
  )
else
  source = Dependabot::Source.new(
    provider: "github",
    repo: repo_name,
    directory: directory,
    branch: nil,
  )
end
puts "Results collected #{DateTime.now}"

# Go through all available package managers
available_package_managers.each do |package_manager|
  ##############################
  # Fetch the dependency files #
  ##############################
  # Name of the package manager
  puts "Fetching #{package_manager} dependency files for #{repo_name}"
  fetcher = Dependabot::FileFetchers.for_package_manager(package_manager).new(
    source: source,
    credentials: credentials,
  )

  begin
    files = fetcher.files
  rescue Dependabot::DependencyFileNotFound
    puts "No dependency files found for #{package_manager}"
    next
  end
  commit = fetcher.commit

  puts "Commit: #{commit}"
  puts "Date of commit: TODO"

  if use_database
    this_project = Project.merge({ title: repo_name.split("/")[-1],
                                  uri: "github.com/#{repo_name}" },
                                on_match: {}, on_create: {}, set: {})
    # q = ActiveGraph::Base.new_query.match_nodes(var: this_project).merge(v: {Version: {version: commit}}).merge('(var)-[h:HAS_VERSION]->(v)')
    # ActiveGraph::Base.query(q)
    # this_project = ActiveGraph::Base.query
    # this_project = Project.create(title: ,
    #                               uri: "github.com/#{repo_name}")
    this_version = this_project.versions.find_or_create_by(version: commit)
    # this_project.versions.merge({version: commit}, on_match: {}, on_create: {}, set: {})
    # this_version = Version.create(version: commit,
    #                               release_date: nil)
  end
  ##############################
  # Parse the dependency files #
  ##############################
  # puts "Parsing dependencies information"
  parser = Dependabot::FileParsers.for_package_manager(package_manager).new(
    dependency_files: files,
    source: source,
    credentials: credentials,
  )

  dependencies = parser.parse

  puts "Found #{dependencies.length} dependencies"

  outdated_dependencies = Array.new

  # dependencies.select(&:top_level?).each do |dep|
  dependencies.each do |dep|
    if use_database
      this_dep =
        this_version.dependencies.find_or_create_by(title: dep.name.split("/")[-1],
                                                    uri: dep.name)
      this_dep_version = this_dep.versions.find_or_create_by(version: dep.version)
    end

    puts "\tName: #{dep.name}, Version: #{dep.version}, Release Date: TODO"

    #########################################
    # Get update details for the dependency #
    #########################################
    checker = Dependabot::UpdateCheckers.for_package_manager(package_manager).new(
      dependency: dep,
      dependency_files: files,
      credentials: credentials,
    )

    next if checker.up_to_date?

    puts "\t\tUpdate available #{checker.latest_version.version}"
  end
end
puts "Done."
