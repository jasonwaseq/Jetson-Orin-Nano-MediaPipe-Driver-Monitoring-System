import subprocess

result = subprocess.check_output(['sh', './model_initializer.sh'])
result.stdout
