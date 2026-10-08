import pydub
from pydub import AudioSegment

def break_up_song_in_telugu(song_path, segment_duration):
    # Load the audio file
    song = AudioSegment.from_file(song_path)

    # Calculate the number of segments
    num_segments = len(song) / segment_duration

    # Create an empty list to store the segments
    segments = []

    # Loop through each segment
    for i in range(int(num_segments)):
        # Get the start and end times of the current segment
        start_time = i * segment_duration
        end_time = (i + 1) * segment_duration

        # Extract the current segment
        segment = song[start_time:end_time]

        # Export the segment to a new file
        segment.export('segment_' + str(i+1) + '.mp3', format='mp3')

# Example usage
break_up_song_in_telugu('path_to_your_song.mp3', 5)  # Break up the song into 5-second segments
