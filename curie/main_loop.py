import subprocess
import time
import os
import shutil  # Import for deleting directories
import psutil
import argparse
from enum import Enum
import json
import re
from datetime import datetime 
import sys
import uuid

# Define an Enum for the baseline values
class Pipeline(Enum):
    OPENHANDS = "openhands"
    CURIE = "curie"
    MAGENTIC = "magentic"

    def __str__(self):
        return self.value  # For proper display in argparse help text

# Define an Enum for the question category values
class QCategory(Enum):
    REASONING = "reasoning"
    VDB = "vdb"
    CLOUD = "cloud"
    MLTRAINING = "mltraining"
    REASONING2 = "reasoning2"
    TESTING = "test"

    def __str__(self):
        return self.value  # For proper display in argparse help text

# Create a function to parse input arguments
def parse_args():
    parser = argparse.ArgumentParser(description="Process input arguments for the script.")
    
    # Add argument for iterations
    parser.add_argument(
        "--iterations",
        type=int,
        required=True,
        help="Number of iterations (must be an integer)."
    )
    
    # Add argument for baseline
    parser.add_argument(
        "--pipeline",
        type=Pipeline,
        choices=list(Pipeline),
        required=True,
        help="Pipeline value (must be one of: openhands, curie)."
    )

    parser.add_argument(
        "--question_file",
        "-f",
        type=str,
        required=True,
        help="Question file to run"
    )

    # Add argument for timeout
    parser.add_argument(
        "--timeout",
        type=int,
        required=True,
        help="Timeout in seconds (must be an integer)."
    )


    parser.add_argument(
        "--task_config",
        type=str, 
        default="configs/base_config.json",
        help="Task configuration file"
    )

    return parser.parse_args()

def get_log_file_openhands(question_file, unique_id, iteration, folder_prefix="llm_reasoning"):
    if not os.path.exists(f"../logs/temp_logs/openhands/{folder_prefix}"):
        os.makedirs(f"../logs/temp_logs/openhands/{folder_prefix}")
    log_filename =  f"../logs/temp_logs/openhands/{folder_prefix}/{os.path.basename(question_file).replace('.txt', '')}_{unique_id}_iter{iteration}.log"
    return os.path.abspath(log_filename) # abs filepath is fine since this refers to the host directory too. 

# Function to create a configuration file
def create_config_file(question_file, unique_id, iteration, task_config):
    log_dir = '../logs/configs' 
    log_filename = f"../logs/{os.path.basename(question_file).replace('.txt', '')}_{unique_id}_iter{iteration}.log"
    config_filename = f"{log_dir}/{task_config['category_name']}_config_{os.path.basename(question_file).replace('.txt', '')}_{unique_id}_iter{iteration}.json"
    task_config.update({"unique_id": unique_id, "iteration": iteration, "log_filename": log_filename, "question_filename": question_file})

    os.makedirs(os.path.dirname(config_filename), exist_ok=True)
    with open(config_filename, "w") as f:
        json.dump(task_config, f, indent=4)
    print(f"Config file created: {config_filename}")
    return config_filename


def docker_image_exists(image):
    """Check if a Docker image exists locally."""
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image], 
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        return result.returncode == 0  # Return True if image exists
    except Exception as e:
        print(f"Error checking Docker image: {e}")
        return False


# Function to run a Docker container
def run_docker_container(unique_id, iteration, task_config):
    rand_uuid = uuid.uuid4()
    container_name = f"exp-agent-container-{unique_id}-{rand_uuid}-iter{iteration}"
    print(f"Building Docker image for iteration {iteration}...")
    
    image_name = task_config["docker_image"]
    docker_filename = task_config["dockerfile_name"]

    if docker_image_exists(image_name):
        print(f"Using existing Docker image: {image_name}")
    else:
        command = [
            "sudo", "docker", "build",
            "--no-cache", "--progress=plain",
            "-t",  image_name,
            "-f",  docker_filename,
            ".."
        ] 
        subprocess.run(command, check=True)
    
    print(f"Running Docker container: {container_name}") 

    # Define the command as a list
    # FIXME: {os.environ['HOME']} is not flexible enough
    command = [
        "docker", "run",
        "-v", "/var/run/docker.sock:/var/run/docker.sock",
        "-v", f"{os.environ['HOME']}/Curie/curie:/curie:ro",
        "-v", f"{os.environ['HOME']}/Curie/benchmark:/benchmark:ro",
        "-v", f"{os.environ['HOME']}/Curie/logs:/logs",
        "-v", f"{os.environ['HOME']}/Curie/starter_file:/workspace",
        "--cpus=4",
        "--memory=8g",
        "--network=host",
        "-d",
        "--name", container_name,
        image_name
    ]
    print(f"Running command: {' '.join(command)}")

    # Run the command
    subprocess.run(command, check=True) 
    return container_name

# Function to execute the experiment inside the Docker container
def execute_experiment_in_container(container_name, config_file):
    """
    Executes the experiment inside the specified Docker container and retrieves log files.

    Args:
        container_name (str): The name of the Docker container.
        config_file (str): The path to the configuration file for the experiment.

    Raises:
        Exception: If any subprocess command fails.
    """
    print(f"Starting experiment in container {container_name} with config: {config_file}")
    try:
        # Run the experiment inside the container
        subprocess.run([
            "docker", "exec", container_name,
            "bash", "-c", f"source ~/.bashrc && source setup/env.sh && conda activate curie && python3 main.py {config_file}"
        ], check=True)  # This will block until main.py finishes. 
        print("Experiment completed successfully.")
        
        # Define source and destination directories
        container_log_dir = "../logs/"  # Directory in the container
        host_log_dir = os.path.expanduser(f"../logs/temp_logs/{task_config.category_name}")  # Host directory
        
        # Ensure host log directory exists
        os.makedirs(host_log_dir, exist_ok=True)
        
        # Run the find and tar command inside the container
        find_tar_cmd = [
            "docker", "exec", container_name,
            "sh", "-c",
            f"cd {container_log_dir} && find . -maxdepth 1 -type f -name '*.log' | tar -cf - -T -"
        ]

        # Use Popen to handle piping
        with subprocess.Popen(find_tar_cmd, stdout=subprocess.PIPE) as proc1:
            subprocess.run(
                ["tar", "-xf", "-", "-C", host_log_dir],
                stdin=proc1.stdout,
                check=True
            )
            proc1.stdout.close()  # Close the stdout pipe after it's used
        
        print(f"Logs successfully extracted to {host_log_dir}")
    
    except subprocess.CalledProcessError as e:
        print(f"Experiment failed with exit code {e.returncode}. Error: {e}")
        raise

# Function to stop and remove the Docker container
def cleanup_docker_container(container_name):
    print(f"Stopping and removing Docker container: {container_name}...")
    subprocess.run(["docker", "stop", container_name], check=True)
    subprocess.run(["docker", "rm", container_name], check=True)
    print(f"Docker container {container_name} cleaned up.")

def run_prune_commands():
    commands = [
        [ "docker", "container", "prune", "-f"],
        [ "docker", "image", "prune", "-f"],
        [ "docker", "volume", "prune", "-f"],
        [ "docker", "builder", "prune", "-f"],
    ]

    for command in commands:
        try:
            print(f"Running docker: {' '.join(command)}")
            result = subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            print(result.stdout.decode())  # Print the standard output
        except subprocess.CalledProcessError as e:
            print(f"Error running command: {' '.join(command)}")
            print(e.stderr.decode())  # Print the standard error
    prune_openhands_docker()
    
def prune_openhands_docker(): 
    # Get the list of container names matching 'openhands'
    result = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}"],
        capture_output=True, text=True, check=True
    )
    # Filter names starting with 'openhands'
    container_names = [name for name in result.stdout.splitlines() if name.startswith("openhands")]

    # If there are containers to remove, run the `docker rm -f` command
    if container_names:
        subprocess.run(["docker", "rm", "-f"] + container_names, check=True)
        print("Removed containers:", ", ".join(container_names))
    else:
        print("No matching containers found.")

def execute_curie(question_file, unique_id, iteration, task_config):
    # Create configuration file
    config_file = create_config_file(question_file, unique_id, iteration, task_config)

    # Run Docker container for this iteration
    container_name = None
    try:
        container_name = run_docker_container(unique_id, iteration, task_config)

        # Execute experiment in Docker container
        execute_experiment_in_container(container_name, config_file)

    finally:
        # Clean up Docker container after each iteration
        if container_name:
            cleanup_docker_container(container_name)
        run_prune_commands()
        # x=1
        pass

# TODO: need a generic cleanup function
def cleanup_vdb_workspace():
    try:
        dirname = "../baselines/openhands/workspace/vector_index_related/starter_file"
        dirname = os.path.abspath(dirname)

        original_dirname = "../starter_file/faiss"
        original_dirname = os.path.abspath(original_dirname)
        # Combine all commands into one shell invocation
        subprocess.run(
            f"cd {dirname} && rm -rf faiss && cp -r {original_dirname} .",
            check=True,
            shell=True
        )
        print("Repository cloned successfully.")
    except subprocess.CalledProcessError as e:
        print(f"An error occurred: {e}")

def cleanup_reasoning_workspace():
    try:
        dirname = "../baselines/openhands/workspace/llm_reasoning_related/starter_file"
        dirname = os.path.abspath(dirname)

        original_dirname = "../starter_file/large_language_monkeys"
        original_dirname = os.path.abspath(original_dirname)
        # Combine all commands into one shell invocation
        subprocess.run(
            f"cd {dirname} && rm -rf large_language_monkeys && cp -r {original_dirname} .",
            check=True,
            shell=True
        )
        print("Repository cloned successfully.")
    except subprocess.CalledProcessError as e:
        print(f"An error occurred: {e}")

def cleanup_reasoning2_workspace():
    try:
        dirname = "../baselines/openhands/workspace/llm_reasoning_2_related/starter_file"
        dirname = os.path.abspath(dirname)

        original_dirname = "../starter_file/The-Impact-of-Reasoning-Step-Length-on-Large-Language-Models"
        original_dirname = os.path.abspath(original_dirname)
        # Combine all commands into one shell invocation
        subprocess.run(
            f"cd {dirname} && rm -rf The-Impact-of-Reasoning-Step-Length-on-Large-Language-Models && cp -r {original_dirname} .",
            check=True,
            shell=True
        )
        print("Repository cloned successfully.")
    except subprocess.CalledProcessError as e:
        print(f"An error occurred: {e}")

def cleanup_ml_training_workspace():
    try:
        dirname = "../baselines/openhands/workspace/ml_training_related/starter_file"
        dirname = os.path.abspath(dirname)

        original_dirname = "../starter_file/MLAgentBench"
        original_dirname = os.path.abspath(original_dirname)
        # Combine all commands into one shell invocation
        subprocess.run(
            f"cd {dirname} && rm -rf MLAgentBench && cp -r {original_dirname} .",
            check=True,
            shell=True
        )
        print("Repository cloned successfully.")
    except subprocess.CalledProcessError as e:
        print(f"An error occurred: {e}")

def cleanup_cloud_workspace():
    try:
        dirname = "../baselines/openhands/workspace/cloud_infra_related/starter_file"
        dirname = os.path.abspath(dirname)

        original_dirname = "../starter_file/cloud_infra"
        original_dirname = os.path.abspath(original_dirname)
        # Combine all commands into one shell invocation
        subprocess.run(
            f"cd {dirname} && rm -rf cloud_infra && cp -r {original_dirname} .",
            check=True,
            shell=True
        )
        print("Repository cloned successfully.")
    except subprocess.CalledProcessError as e:
        print(f"An error occurred: {e}")

def execute_openhands(question_file, unique_id, iteration, folder_prefix="llm_reasoning"):
    # question_file is an absolute path filename

    # Cleanup workspace:
    if folder_prefix == "llm_reasoning":
        cleanup_reasoning_workspace()
    elif folder_prefix == "vector_index":
        cleanup_vdb_workspace()
    elif folder_prefix == "cloud_infra":
        cleanup_cloud_workspace()
    elif folder_prefix == "ml_training":
        cleanup_ml_training_workspace()
    elif folder_prefix == "llm_reasoning_2":
        cleanup_reasoning2_workspace()

    # Get log file for openhands:
    log_filename = get_log_file_openhands(question_file, unique_id, iteration, folder_prefix)
    print(f"Log file for openhands: {log_filename}")
    openhands_dir = "~/OpenHands"
    openhands_dir = os.path.expanduser(openhands_dir)

    common_txt_filename = f"~/langgraph-exp-agent/benchmark/common.txt"
    common_txt_filename = os.path.expanduser(common_txt_filename)

    # Read from common_txt_filename:
    question_text = ""
    with open(common_txt_filename, 'r') as f:
        question_text = f.read()
    # Read from question_file: but replace all directories with the correct directory. LLM reasoning qs now are specified in terms of the docker container filepaths, we need to convert them into host paths for openhands, and they also need to be in the workspace. 
    with open(question_file, 'r') as f:
        q_temp = f.read()
        # starter_file_dir = "~/langgraph-exp-agent/starter_file"
        # starter_file_dir = os.path.expanduser(starter_file_dir)
        starter_file_dir = f"{folder_prefix}_related/starter_file"
        # Convert all occurences of "/starter_file" to starter_file_dir (which may be something like llm_reasoning_related/starter_file) in the question file:
        q_temp = q_temp.replace("/starter_file", starter_file_dir)

        if folder_prefix == "llm_reasoning_2":
            with open("setup/env.sh", 'r') as f:
                env_credentials = f.read()

            # Convert all occurences of the following:
            q_temp = q_temp.replace("source /exp_agent/setup/env.sh", env_credentials)
            q_temp = q_temp.replace("conda activate impact", f"conda activate impact; pip install torch==1.8.2+cu111 torchtext==0.9.2 -f https://download.pytorch.org/whl/lts/1.8/torch_lts.html; pip install -r {starter_file_dir}/The-Impact-of-Reasoning-Step-Length-on-Large-Language-Models/requirements.txt")

        question_text += q_temp
    # Save to a new file:
    updated_ques_filename = f"~/langgraph-exp-agent/eval_metadata/temp_scripts/openhands/{os.path.basename(question_file)}"
    updated_ques_filename = os.path.expanduser(updated_ques_filename)
    with open(updated_ques_filename, 'w') as f:
        f.write(question_text)

    # Construct the command
    command = f"""
export LOG_ALL_EVENTS=true
cd {openhands_dir}
poetry run python -m openhands.core.main -f {updated_ques_filename} 2>&1 | tee -a {log_filename}
    """

    # Execute the command
    try:
        print(f"Executing OpenHands pipeline for question {question_file}, iteration {iteration}...")
        subprocess.run(command, shell=True, check=True, executable="/bin/bash")
        print(f"Completed iteration {iteration} for question {question_file}. Logs stored in {log_filename}")
    except subprocess.CalledProcessError as e:
        print(f"Error executing pipeline for {question_file}, iteration {iteration}: {e}")
        raise

def run_magentic_docker_container(unique_id, iteration, PROMPT_FILE, folder_prefix="llm_reasoning"):
    rand_uuid = uuid.uuid4()
    container_name = f"magentic-agent-container-{unique_id}-{rand_uuid}-iter{iteration}"
    print(f"Building Docker image for iteration {iteration}...")
    
    image_name = "magentic-agent-image"
    docker_filename = "MagenticDockerfile"

    command = [
        "docker", "build",
        "--build-arg", f"PROMPT_FILE={PROMPT_FILE}",
        "-t", "magentic-agent-image",
        "-f", "MagenticDockerfile",
        ".."
    ]

    subprocess.run(command, check=True)

    print(f"Running Docker container: {container_name}")
    command = [
        "docker", "run",
        "-v", "/var/run/docker.sock:/var/run/docker.sock",
        "-v", "/usr/bin/docker:/usr/bin/docker",
        "--cpus=4",
        "--memory=8g",
        "--network=host",
        "-d",
        "--name", container_name,
        image_name
    ]

    subprocess.run(command, check=True)
    return container_name

def execute_experiment_in_magentic_container(container_name, question_file, folder_prefix="llm_reasoning"):
    """
    Executes the experiment inside the specified Docker container and retrieves log files.

    Args:
        container_name (str): The name of the Docker container.
        config_file (str): The path to the configuration file for the experiment.

    Raises:
        Exception: If any subprocess command fails.
    """
    print(f"Starting experiment in container {container_name} with question_file: {question_file}")
    try:
        # Run the experiment inside the container
        subprocess.run([
            "docker", "exec", container_name,
            "bash", "-c", f"source ~/.bashrc && python /starter_file/autogen/python/packages/autogen-magentic-one/examples/example.py  --logs_dir /temp/logs"
        ], check=True)  # This will block until main.py finishes
        print("Experiment completed successfully.")
        
        # Define source and destination directories
        container_log_dir = "/temp/logs/"  # Directory in the container
        host_log_dir = os.path.expanduser(f"../eval_metadata/temp_logs/magentic/{folder_prefix}")  # Host directory
        
        # Ensure host log directory exists
        os.makedirs(host_log_dir, exist_ok=True)
        
        # Run the find and tar command inside the container
        find_tar_cmd = [
            "docker", "exec", container_name,
            "sh", "-c",
            f"cd {container_log_dir} && find . -maxdepth 1 -type f -name '*.jsonl' | tar -cf - -T -"
        ]

        log_suffix = os.path.basename(question_file).replace('.txt', '')

        rename_and_tar_cmd = [
            "docker", "exec", container_name,
            "sh", "-c",
            f"""
            cd {container_log_dir} && \
            find . -maxdepth 1 -type f -name '*.jsonl' -exec sh -c '
                for file; do
                    mv "$file" "${{file%.jsonl}}_{log_suffix}.jsonl";
                done
            ' _ {{}} + && \
            find . -maxdepth 1 -type f -name '*.jsonl' | tar -cf - -T -
            """
        ]

        # Use Popen to handle piping
        with subprocess.Popen(rename_and_tar_cmd, stdout=subprocess.PIPE) as proc1:
            subprocess.run(
                ["tar", "-xf", "-", "-C", host_log_dir],
                stdin=proc1.stdout,
                check=True
            )
            proc1.stdout.close()  # Close the stdout pipe after it's used
        
        print(f"Logs successfully extracted to {host_log_dir}")
    
    except subprocess.CalledProcessError as e:
        print(f"Experiment failed with exit code {e.returncode}. Error: {e}")
        raise

def execute_magentic(question_file, unique_id, iteration, folder_prefix="llm_reasoning"):
    # Get log file for openhands:

    common_txt_filename = f"~/langgraph-exp-agent/benchmark/common.txt"
    common_txt_filename = os.path.expanduser(common_txt_filename)

    # Read from common_txt_filename:
    question_text = ""
    with open(common_txt_filename, 'r') as f:
        question_text = f.read()
    # Read from question_file: but replace all directories with the correct directory. LLM reasoning qs now are specified in terms of the docker container filepaths, we need to convert them into host paths for openhands, and they also need to be in the workspace. 
    with open(question_file, 'r') as f:
        q_temp = f.read()

        if folder_prefix == "llm_reasoning_2":
            # Convert all occurences of the following:
            q_temp = q_temp.replace("conda activate impact", f"conda activate impact; pip install torch==1.8.2+cu111 torchtext==0.9.2 -f https://download.pytorch.org/whl/lts/1.8/torch_lts.html; pip install -r /starter_file/The-Impact-of-Reasoning-Step-Length-on-Large-Language-Models/requirements.txt")

        question_text += q_temp
    # Save to a new file:
    updated_ques_filename = f"../eval_metadata/temp_scripts/magentic/{os.path.basename(question_file)}"
    if not os.path.exists("../eval_metadata/temp_scripts/magentic/"):
        os.makedirs("../eval_metadata/temp_scripts/magentic/")
    updated_ques_filename = os.path.expanduser(updated_ques_filename)
    with open(updated_ques_filename, 'w') as f:
        f.write(question_text)
    
    PROMPT_FILE = f"/eval_metadata/temp_scripts/magentic/{os.path.basename(question_file)}"

    # Run Docker container for this iteration
    container_name = None
    try:
        container_name = run_magentic_docker_container(unique_id, iteration, PROMPT_FILE, folder_prefix)

        # Execute experiment in Docker container
        execute_experiment_in_magentic_container(container_name, question_file, folder_prefix)

    finally:
        # Clean up Docker container after each iteration
        if container_name:
            cleanup_docker_container(container_name)
        run_prune_commands()
        # x=1

# Main function
def main():
    args = parse_args()
    
    print(f"Iterations: {args.iterations}")
    print(f"Pipeline: {args.pipeline}")
    print(f"Timeout: {args.timeout} seconds")
    config_file = args.task_config
    question_file = args.question_file
    # read from config
    try:
        with open(config_file, 'r') as f:
            task_config = json.load(f)
            print(f"Config: {task_config}")
    except Exception as e:
        print(f"Error reading config file: {e}")
        return
    
    print(f"Processing {question_file} for {args.iterations} iterations...")
    for iteration in range(1, args.iterations + 1):
        # Perform the required operation for each iteration (to be provided later)
        print(f"Iteration {iteration} ")
        start_time = time.time() 
        unique_id = datetime.now().strftime("%Y%m%d%H%M%S")

        if args.pipeline == Pipeline.OPENHANDS:
            question_file = os.path.abspath(question_file) # question_file is a relative path in this format: ../benchmark/llm_reasoning/q4_target_coverage.txt. We convert it to an abs path for openhands since we need to pass in the abs dir. 
            execute_openhands(question_file, unique_id, iteration, folder_prefix)
        elif args.pipeline == Pipeline.CURIE:
            execute_curie(question_file, unique_id, iteration, task_config)
        elif args.pipeline == Pipeline.MAGENTIC:
            execute_magentic(question_file, unique_id, iteration, folder_prefix)

        end_time = time.time()
        elapsed_time = end_time - start_time
        print(f"Iteration {iteration} for {question_file} completed in {elapsed_time:.2f} seconds.")

if __name__ == "__main__":
    # Create a main loop log file based on a uuid:
    # uuid_time = datetime.now().strftime("%Y%m%d%H%M%S")
    # if not os.path.exists("../logs/main_loop"):
    #     os.makedirs("../logs/main_loop")

    # main_loop_log_filename = f"../logs/main_loop/main_loop_logs_{uuid_time}.log" # logs main loop metadata
    # main_loop_log_file = open(main_loop_log_filename, 'w')
    # print(f"Main loop log file: {main_loop_log_filename}")
    # sys.stdout = main_loop_log_file
    # sys.stderr = main_loop_log_file

    main()

    # # Reset stdout and stderr
    # sys.stdout = sys.__stdout__
    # sys.stderr = sys.__stderr__

    # print(f"Output logged to {main_loop_log_filename}")