# This script is designed to loop through all dependencies in a GHE, GitLab or
# Azure DevOps project

require 'date'
require "dependabot/file_fetchers"
require "dependabot/file_parsers"
require "dependabot/update_checkers"
require "dependabot/file_updaters"
require "dependabot/pull_request_creator"
require "dependabot/omnibus"
require "gitlab"

require 'optparse'

options = {}
OptionParser.new do |opts|
  opts.banner = "Usage: --repo-name NAME --directory DIR"

  opts.on('-r', '--repo-name NAME', 'Github Repo Name') { |v| options[:repo_name] = v }
  opts.on('-d', '--directory DIR', 'Directory with dependency files') { |v| options[:directory] = v }

end.parse!

repo_name = options[:repo_name]
directory = options[:directory]

puts repo_name
puts directory

credentials = [
  {
    "type" => "git_source",
    "host" => "github.com",
    "username" => "x-access-token",
    "password" => ENV["GITHUB_ACCESS_TOKEN"] # A GitHub access token with read access to public repos
  }
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
if false
  database_host = 'host.docker.internal'
  database_port = 5555
  database_name = 'sieve'
  table_name = 'dependabot_results'
  database_user = 'postgres'
  database_password = 'example'
  conn = PG.connect( host: database_host, port: database_port, user: database_user, password: database_password)

  # Check if database exists
  ret = conn.exec %Q{
          SELECT datname
          FROM pg_catalog.pg_database
          WHERE lower(datname) = lower('%s')
        } % [ database_name ]

  # If no results are returned, then we need to create the database
  if ret.ntuples == 0
    puts("Creating database: #{database_name} ...")
    # TODO(ek) Check for error?
    conn.exec %Q{
      CREATE DATABASE "%s"
    } % [ database_name ]
    puts("Created database.")
  end

  conn.exec %Q{
    CONNECT TO "%s"
  } % [ database_name ]

  # Make sure our table exists
  conn.exec %Q{
    SELECT EXISTS
      (. SELECT
        FROM
          pg_catalog.pg_class c. JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace. WHERE n.nspname = 'dependabot'. AND c.relname = 'table_name'. AND c.relkind = 'r'. );
  }
end

if ENV["GITHUB_ENTERPRISE_ACCESS_TOKEN"]
  credentials << {
    "type" => "git_source",
    "host" => ENV["GITHUB_ENTERPRISE_HOSTNAME"], # E.g., "ghe.mydomain.com",
    "username" => "x-access-token",
    "password" => ENV["GITHUB_ENTERPRISE_ACCESS_TOKEN"] # A GHE access token with API permission
  }

  source = Dependabot::Source.new(
    provider: "github",
    hostname: ENV["GITHUB_ENTERPRISE_HOSTNAME"],
    api_endpoint: "https://#{ENV['GITHUB_ENTERPRISE_HOSTNAME']}/api/v3/",
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
    "password" => ENV["GITLAB_ACCESS_TOKEN"] # A GitLab access token with API permission
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
    "password" => ENV["AZURE_ACCESS_TOKEN"]
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
